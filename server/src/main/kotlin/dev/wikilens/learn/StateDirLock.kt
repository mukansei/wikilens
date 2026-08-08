package dev.wikilens.learn

import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.channels.OverlappingFileLockException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import org.slf4j.LoggerFactory

/**
 * 상태 디렉터리를 **한 프로세스만** 쓰도록 잠근다.
 *
 * 없으면 같은 `state-dir` 로 서버를 둘 띄우는 것이 그냥 된다. Lucene 의 `write.lock` 은
 * 상시 보호가 아니다 — `rebuild` 가 `IndexWriter(d, cfg).use { }` 로 열었다 닫으므로
 * **재색인하는 동안만** 잡혀 있고, 그 밖의 시간에는 아무 방어가 없다.
 *
 * 두 프로세스가 붙으면 이렇게 된다:
 *
 *   - 포스팅은 각자의 힙에 있으므로 **서로의 학습을 재기동 전까지 못 본다.**
 *     같은 질의가 어느 프로세스에 붙느냐에 따라 다른 답을 준다.
 *   - 둘 다 같은 `trajectories.jsonl` 에 append 한다. 줄이 섞이지는 않지만
 *     (`O_APPEND` 원자성) **각자 절반만 아는 상태**가 된다.
 *   - `/api/stats` 가 두 값을 오간다 — 진단이 진단을 못 한다.
 *
 * **여기서는 기동을 막는다.** 볼트를 못 읽는 경우와 다르다 — 그건 고치면 되는 설정
 * 오류라 뜨는 편이 낫지만(`--status` 로 진단할 길이 남는다), 이건 **이미 정상 동작
 * 중인 서버가 있다**는 뜻이고 두 번째를 띄우면 학습이 조용히 갈린다. 첫 서버가
 * 살아 있으므로 진단 경로도 사라지지 않는다.
 *
 * 락은 프로세스 수명 동안 잡고 있는다 — 닫지 않는다. 프로세스가 어떻게 끝나든
 * (SIGKILL 포함) OS 가 풀어주므로, 죽은 서버의 락이 남아 다음 기동을 막는 일은 없다.
 */
class StateDirLock(stateDir: Path) {

    class AlreadyRunning(message: String) : IllegalStateException(message)

    private val path: Path = stateDir.resolve(".lock")

    /** GC 되면 락이 풀린다. 쓰지 않아도 참조를 붙잡고 있어야 한다. */
    private val channel: FileChannel
    private val held: FileLock

    init {
        Files.createDirectories(stateDir)
        val ch = FileChannel.open(path, StandardOpenOption.CREATE, StandardOpenOption.WRITE)
        val lock = try {
            ch.tryLock()
        } catch (e: OverlappingFileLockException) {
            // 같은 JVM 이 이미 잡은 경우. `tryLock` 은 null 이 아니라 예외를 던진다.
            null
        }
        if (lock == null) {
            ch.close()
            throw AlreadyRunning(
                "다른 WikiLens 서버가 이미 이 상태 디렉터리를 쓰고 있습니다: $path — " +
                    "두 프로세스가 붙으면 각자 다른 포스팅을 들고 같은 궤적 로그에 쓰게 되어 " +
                    "학습이 조용히 갈립니다. 그 서버를 끄거나 --wikilens.state-dir 를 다르게 주세요."
            )
        }
        channel = ch
        held = lock
        warnIfNetworkFileSystem(stateDir)
    }

    /**
     * 네트워크 파일시스템이면 경고한다.
     *
     * 궤적 로그는 `O_APPEND` 원자성에 의존한다 — NFS 에서는 그 보장이 없어 **줄이
     * 섞인다.** 그동안 이 사실은 `FileTrajectorySink` 주석에만 있었고, 운영자가
     * `state-dir` 를 NFS 에 두면 아무도 모르게 로그가 망가졌다.
     *
     * **막지는 않는다.** 파일시스템 판정은 플랫폼마다 다르고 오탐이 기동을 막으면
     * 그게 더 나쁘다 — 락 충돌과 달리 여기서는 "정말 안전한가" 를 우리가 확신할 수 없다.
     */
    private fun warnIfNetworkFileSystem(dir: Path) {
        val type = runCatching { Files.getFileStore(dir).type().lowercase() }.getOrNull() ?: return
        if (NETWORK_FS.none { it in type }) return
        LoggerFactory.getLogger(javaClass).warn(
            "상태 디렉터리가 네트워크 파일시스템입니다 ({} — {}): 궤적 로그는 O_APPEND " +
                "원자성에 의존하므로 **줄이 섞일 수 있습니다.** 로컬 디스크로 옮기세요.",
            type, dir,
        )
    }

    companion object {
        private val NETWORK_FS = listOf("nfs", "smb", "cifs", "afp", "webdav", "fuse")
    }

    /** 잠근 자리. 진단 로그용. */
    override fun toString(): String = path.toString()
}
