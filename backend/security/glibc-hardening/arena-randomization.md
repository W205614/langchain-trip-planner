# Experimental thread-arena placement hardening

Reviewed target: Debian glibc 2.41 on Linux x86_64. This is a downstream
experiment for CVE-2019-1010025 / BZ 22853, not an accepted upstream security
release or a claim of a complete allocator audit.

The starting design is Ismael Ripoll and Hector Marco-Gisbert's
[2020-03-25 v2 proposal](https://sourceware.org/pipermail/libc-alpha/2020-March/112127.html).
Their proposal restores a process-wide page-aligned residue to heap addresses.
The old fixed HEAP_MAX_SIZE arithmetic cannot be copied unchanged into 2.41:
explicit huge pages change heap_max_size at startup, mappings can fall back to
ordinary pages, and cached aligned_heap_area mappings must obey the same grid.

This adaptation changes only malloc/arena.c:

* Every non-main heap starts at arena_offset modulo runtime heap_max_size.
  Header lookup subtracts that offset before alignment and restores it after.
  heap_info and all public structures/symbols remain unchanged.
* The offset is chosen after the hugetlb tunable is resolved. It is a multiple
  of the configured explicit huge-page size, or the ordinary page size.
  Huge-page allocation and its ordinary-page fallback therefore share one
  unambiguous header lookup. The 2×max reservation, cached hint and single-max
  fallback are all checked against that residue.
* The process-wide offset never changes on thread transfer, heap trimming or
  fork. A child must keep resolving inherited chunks to the same old headers;
  changing it on fork would corrupt cross-thread or inherited frees.

## Independent entropy and failure behavior

The v2 proposal reused low bits of a libc address, correlating the residue with
the libc mapping. This adaptation does **not** use a libc address, pointer guard,
stack canary or clock. It reads one uintptr_t through glibc 2.41's existing
__getrandom_nocancel_nostatus_direct helper with GRND_NONBLOCK. This is a direct,
non-cancelable syscall that does not allocate vDSO state or modify errno.

Any result other than the full requested size terminates initialization with
`malloc: cannot initialize arena randomization`. There is no fixed-offset or
clock/address fallback. In particular, an unseeded kernel RNG (EAGAIN), syscall
denial (EPERM), an unsupported kernel (ENOSYS) or a short read prevents the
process from continuing at its first allocator initialization. This availability
contract must be accepted for the actual supported runtime; it is not hidden
behind a success record. The fault regression uses real seccomp denial before
the first explicit malloc and checks that the original libc proceeds while the
hardened libc aborts with this distinct message.

The offset remains quantized by the required page alignment. With 64MiB heaps
and 4KiB pages there are 16384 possible residues; explicit huge-page heaps use
four huge pages and have four possible residues. Zero is a valid random result.
This does not create independent per-arena entropy or change the address-space
inheritance contract of fork, and a process memory disclosure can still reveal
the offset. These are bounded hardening properties, not an exploit-proof claim.

## Build and verification

Apply arena-randomization.patch with fuzz zero to the reviewed 2.41 source after
the existing mature backports, then rebuild the same glibc configuration.
Compare every versioned dynamic export against the pre-change libc. Compile
arena-regression.c with `gcc -std=gnu11 -O2 -g -Wall -Wextra -Werror -pthread`.
Run it with the newly built loader and library path, not the host's libc.

The regression forces four arenas through more than one ordinary heap's worth
of allocations, checks malloc/calloc/realloc/aligned allocations, frees from
other threads, trims, forks with live worker allocations, frees inherited data
and creates another thread in the child. It observes actual /proc/self/maps
ranges; it does not reproduce heap_for_ptr's computation as a fake allocator.
Run hugetlb tunables 0, 1 and 2, and compare fresh-process heap residues with the
original libc. Zero residues are allowed, so observe a batch rather than demand
that each random run be nonzero. Run the relevant upstream malloc alignment,
cross-thread, fork, realloc and trim checks as well.

The current lab kernel reports HugePages_Total=0. hugetlb=2 tests therefore
exercise the real failed MAP_HUGETLB/ordinary-page fallback contract, not a
successful physical huge-page allocation. Successful reserved huge-page use
requires a suitable separate environment and must not be reported as verified
by those fallback runs. Final integration with the separate TLS/stack hardening
also requires rerunning the allocator and application concurrency checks.

Keep the complete glibc/Debian corresponding source, the original proposal's
attribution, this patch, independent checksum manifest, test and build evidence.
The patch contains glibc-derived LGPL-2.1-or-later code. No component version or
public symbol is relabeled as fixed by this standalone prototype.
