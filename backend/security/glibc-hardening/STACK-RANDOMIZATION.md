# Experimental x86-64 NPTL stack randomization

Status: implementation and isolated validation in progress, not an accepted
release artifact. The user authorized this additional hardening on 2026-11-10.
This is a downstream change, not an upstream glibc security release.

Disabling stack caching alone does not address CVE-2019-1010024: a new Linux
`mmap(NULL, ...)` may choose the same or a predictable adjacent address. This
proposal disables cached-stack reuse and uses independent kernel-CSPRNG
page-aligned hints for each system-allocated stack. It accepts only the exact
requested address, without MAP_FIXED. Alternate mappings are unmapped before
another bounded attempt.

Entropy failure, mapping errors or exhaustion of 32 attempts fail thread
creation; there is no predictable fallback. The raw non-cancelling getrandom
syscall does not add a cancellation point to pthread_create. It requires Linux
getrandom support and initialized entropy. The address window is 4 GiB to
64 TiB within x86-64's low canonical range. Larger mappings fail with ENOMEM.
Caller-provided stacks retain their contract. The cache tunable cannot
re-enable reuse. This trades cache performance for fresh mappings; it does not
promise unchanged performance or protection against all address disclosure.

Apply the TLS guard patch first, preserving its guard placement, stack
accounting and public ABI. Verify real pthread create/join, concurrency, fork,
different stack/guard sizes, reclamation and subprocess-only entropy-failure
injection. The reuse check must distinguish the original library. Statistical
observations support, but do not replace, review of the actual source checks.

Primary contracts: [mmap](https://man7.org/linux/man-pages/man2/mmap.2.html),
[getrandom](https://man7.org/linux/man-pages/man2/getrandom.2.html), and the
[upstream cache discussion](https://sourceware.org/legacy-ml/libc-alpha/2019-11/msg00051.html).

The helper and glibc changes use LGPL-2.1-or-later. Include original source,
changes, notices and build/test instructions in the corresponding-source
distribution. Do not publish before regressions and compatibility checks pass.
