/*
 * LD_PRELOAD shim that appends Chromium switches to WeChat's webview process.
 *
 * Why this exists: the Channels page renders in RadiumWMPF/WeChatAppEx, a Chromium 122
 * fork that verifies server certificates against a root store compiled into its own
 * binary. It never opens an NSS database in the process that does the verifying, so
 * installing the wxclip CA into ~/.pki/nssdb, the system anchors or p11-kit has no
 * effect and the page fails with "your connection is not private" (observed 2026-09-11,
 * see docs/项目背景.md).
 *
 * WeChatAppEx does support --ignore-certificate-errors-spki-list, which makes Chromium
 * accept a chain containing one specific public key - far narrower than turning
 * certificate checking off. But the argv is built inside libwmpf_host_export.so, which
 * has no environment variable or config file for extra switches. So we intercept the
 * exec that launches it and append the switches ourselves.
 *
 * Preloaded into every process in the sandbox, it only acts on execs whose program name
 * matches WXCLIP_APPEX_TARGET; everything else passes straight through.
 *
 *   WXCLIP_APPEX_FLAGS   space-separated switches to append (no-op when unset)
 *   WXCLIP_APPEX_TARGET  program name to match, default WeChatAppEx
 *   WXCLIP_APPEX_DEBUG   set to 1 to log every rewrite to stderr
 *
 * Build with scripts/build-appex-preload.sh.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MAX_EXTRA 16

static const char *program_name(const char *path) {
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

static int is_target(const char *path) {
    const char *want = getenv("WXCLIP_APPEX_TARGET");
    if (!path) return 0;
    if (!want || !*want) want = "WeChatAppEx";
    return strcmp(program_name(path), want) == 0;
}

/* True when argv already carries this switch, so a re-exec does not stack duplicates. */
static int already_present(char *const argv[], const char *flag) {
    size_t name_len = strcspn(flag, "=");
    for (int i = 0; argv[i]; i++) {
        if (strncmp(argv[i], flag, name_len) == 0
            && (argv[i][name_len] == '\0' || argv[i][name_len] == '=')) {
            return 1;
        }
    }
    return 0;
}

/*
 * Returns a new argv with the configured switches appended, or NULL to use argv as is.
 * The caller is about to exec, so the allocation is deliberately never freed.
 */
static char **argv_with_flags(const char *path, char *const argv[]) {
    const char *flags = getenv("WXCLIP_APPEX_FLAGS");
    if (!flags || !*flags || !is_target(path) || !argv || !argv[0]) return NULL;

    char *copy = strdup(flags);
    if (!copy) return NULL;

    char *extra[MAX_EXTRA];
    int extra_count = 0;
    char *save = NULL;
    for (char *tok = strtok_r(copy, " \t", &save); tok; tok = strtok_r(NULL, " \t", &save)) {
        if (extra_count == MAX_EXTRA || already_present(argv, tok)) continue;
        extra[extra_count++] = tok;
    }
    if (extra_count == 0) {
        free(copy);
        return NULL;
    }

    int argc = 0;
    while (argv[argc]) argc++;
    char **out = calloc((size_t)argc + (size_t)extra_count + 1, sizeof(char *));
    if (!out) {
        free(copy);
        return NULL;
    }
    for (int i = 0; i < argc; i++) out[i] = argv[i];
    for (int i = 0; i < extra_count; i++) out[argc + i] = extra[i];

    if (getenv("WXCLIP_APPEX_DEBUG")) {
        fprintf(stderr, "[wxclip-preload] %s += ", program_name(path));
        for (int i = 0; i < extra_count; i++) fprintf(stderr, "%s ", extra[i]);
        fprintf(stderr, "\n");
        fflush(stderr);
    }
    return out;
}

int execv(const char *path, char *const argv[]) {
    static int (*real)(const char *, char *const[]);
    if (!real) real = dlsym(RTLD_NEXT, "execv");
    char **patched = argv_with_flags(path, argv);
    return real(path, patched ? patched : argv);
}

int execvp(const char *file, char *const argv[]) {
    static int (*real)(const char *, char *const[]);
    if (!real) real = dlsym(RTLD_NEXT, "execvp");
    char **patched = argv_with_flags(file, argv);
    return real(file, patched ? patched : argv);
}

int execve(const char *path, char *const argv[], char *const envp[]) {
    static int (*real)(const char *, char *const[], char *const[]);
    if (!real) real = dlsym(RTLD_NEXT, "execve");
    char **patched = argv_with_flags(path, argv);
    return real(path, patched ? patched : argv, envp);
}

int posix_spawn(pid_t *pid, const char *path, const posix_spawn_file_actions_t *actions,
                const posix_spawnattr_t *attrp, char *const argv[], char *const envp[]) {
    static int (*real)(pid_t *, const char *, const posix_spawn_file_actions_t *,
                       const posix_spawnattr_t *, char *const[], char *const[]);
    if (!real) real = dlsym(RTLD_NEXT, "posix_spawn");
    char **patched = argv_with_flags(path, argv);
    return real(pid, path, actions, attrp, patched ? patched : argv, envp);
}
