/* Copyright (C) 2026 W205614. SPDX-License-Identifier: LGPL-2.1-or-later */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <pthread.h>
#include <sched.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

struct observation {
  void *base;
  size_t size, guard;
  int identity;
  pthread_barrier_t *barrier;
};
static _Thread_local unsigned int tls_marker;

static void *
observe_stack (void *argument)
{
  struct observation *result = argument;
  pthread_attr_t attributes;
  volatile unsigned char local_marker = 17;
  assert (pthread_getattr_np (pthread_self (), &attributes) == 0);
  assert (pthread_attr_getstack (&attributes, &result->base, &result->size) == 0);
  assert (pthread_attr_getguardsize (&attributes, &result->guard) == 0);
  assert ((uintptr_t) &local_marker >= (uintptr_t) result->base);
  assert ((uintptr_t) &local_marker < (uintptr_t) result->base + result->size);
  assert (pthread_attr_destroy (&attributes) == 0);
  tls_marker = (unsigned int) result->identity + 73;
  if (result->barrier != NULL) {
    int status = pthread_barrier_wait (result->barrier);
    assert (status == 0 || status == PTHREAD_BARRIER_SERIAL_THREAD);
  }
  for (unsigned int index = 0; index < 100; ++index) {
    sched_yield ();
    assert (tls_marker == (unsigned int) result->identity + 73);
  }
  return NULL;
}

static void
one_thread (pthread_attr_t *attributes, struct observation *result)
{
  pthread_t thread;
  assert (pthread_create (&thread, attributes, observe_stack, result) == 0);
  assert (pthread_join (thread, NULL) == 0);
}

static void
check_contracts (void)
{
  const size_t sizes[] = {65536, 1048576, 8388608, 16777216};
  const size_t guards[] = {0, 4096, 16384};
  for (size_t size = 0; size < sizeof sizes / sizeof *sizes; ++size)
    for (size_t guard = 0; guard < sizeof guards / sizeof *guards; ++guard) {
      pthread_attr_t attributes;
      struct observation result = {0};
      assert (pthread_attr_init (&attributes) == 0);
      assert (pthread_attr_setstacksize (&attributes, sizes[size]) == 0);
      assert (pthread_attr_setguardsize (&attributes, guards[guard]) == 0);
      one_thread (&attributes, &result);
      assert (result.size == sizes[size] && result.guard == guards[guard]);
      assert (pthread_attr_destroy (&attributes) == 0);
    }
  /* The library must not take ownership of caller-provided storage. */
  size_t bytes = 1048576;
  void *storage = mmap (NULL, bytes, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  assert (storage != MAP_FAILED);
  pthread_attr_t attributes;
  struct observation supplied = {0};
  assert (pthread_attr_init (&attributes) == 0);
  assert (pthread_attr_setstack (&attributes, storage, bytes) == 0);
  one_thread (&attributes, &supplied);
  assert (supplied.base == storage && supplied.size == bytes);
  unsigned char residency;
  assert (mincore (storage, (size_t) sysconf (_SC_PAGESIZE), &residency) == 0);
  assert (munmap (storage, bytes) == 0);
  assert (pthread_attr_destroy (&attributes) == 0);

  enum { workers = 32 };
  pthread_t threads[workers];
  struct observation observations[workers] = {0};
  pthread_barrier_t barrier;
  assert (pthread_barrier_init (&barrier, NULL, workers + 1) == 0);
  for (int index = 0; index < workers; ++index) {
    observations[index].identity = index;
    observations[index].barrier = &barrier;
    assert (pthread_create (&threads[index], NULL, observe_stack,
                            &observations[index]) == 0);
  }
  int status = pthread_barrier_wait (&barrier);
  assert (status == 0 || status == PTHREAD_BARRIER_SERIAL_THREAD);
  for (int left = 0; left < workers; ++left)
    for (int right = left + 1; right < workers; ++right)
      assert ((uintptr_t) observations[left].base + observations[left].size
                <= (uintptr_t) observations[right].base
              || (uintptr_t) observations[right].base + observations[right].size
                <= (uintptr_t) observations[left].base);
  for (int index = 0; index < workers; ++index)
    assert (pthread_join (threads[index], NULL) == 0);
  assert (pthread_barrier_destroy (&barrier) == 0);
  pid_t child = fork ();
  assert (child >= 0);
  if (child == 0) {
    for (int index = 0; index < 8; ++index) {
      struct observation result = {0};
      one_thread (NULL, &result);
    }
    _exit (0);
  }
  assert (waitpid (child, &status, 0) == child);
  assert (WIFEXITED (status) && WEXITSTATUS (status) == 0);
}

static int
entropy_failure (void)
{
  /* Prime malloc before the subprocess-only filter so its early entropy
     initialization is not confused with the pthread stack under test. */
  void *volatile initialized = malloc (32);
  assert (initialized != NULL);
  *(volatile unsigned char *) initialized = 1;
  free (initialized);
  struct sock_filter instructions[] = {
    BPF_STMT (BPF_LD | BPF_W | BPF_ABS,
              (unsigned int) offsetof (struct seccomp_data, nr)),
    BPF_JUMP (BPF_JMP | BPF_JEQ | BPF_K, __NR_getrandom, 0, 1),
    BPF_STMT (BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EAGAIN),
    BPF_STMT (BPF_RET | BPF_K, SECCOMP_RET_ALLOW)
  };
  struct sock_fprog program = {
    .len = sizeof instructions / sizeof *instructions, .filter = instructions
  };
  assert (prctl (PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0);
  assert (prctl (PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program) == 0);
  pthread_t thread;
  struct observation result = {0};
  int status = pthread_create (&thread, NULL, observe_stack, &result);
  if (status == 0)
    assert (pthread_join (thread, NULL) == 0);
  printf ("{\"entropy_denied_pthread_status\":%d}\n", status);
  return status == EAGAIN ? 0 : 1;
}

int
main (int argc, char **argv)
{
  if (argc == 2 && strcmp (argv[1], "entropy-denied") == 0)
    return entropy_failure ();
  int verify = argc == 2 && strcmp (argv[1], "verify") == 0;
  enum { count = 128 };
  struct observation observations[count] = {0};
  int unique = 0, still_mapped = 0;
  size_t pagesize = (size_t) sysconf (_SC_PAGESIZE);
  for (int index = 0; index < count; ++index) {
    one_thread (NULL, &observations[index]);
    int seen = 0;
    for (int previous = 0; previous < index; ++previous)
      seen |= observations[previous].base == observations[index].base;
    unique += !seen;
    unsigned char residency;
    errno = 0;
    int mapped = mincore (observations[index].base, pagesize, &residency);
    assert (mapped == 0 || (mapped == -1 && errno == ENOMEM));
    still_mapped += mapped == 0;
    if (verify) {
      assert ((uintptr_t) observations[index].base >= (UINT64_C (1) << 32));
      assert ((uintptr_t) observations[index].base + observations[index].size
                <= (UINT64_C (1) << 46));
    }
  }
  printf ("{\"sequential_threads\":%d,\"unique_stacks\":%d,\"mapped_after_join\":%d}\n",
           count, unique, still_mapped);
  if (verify) {
    assert (unique >= 96 && still_mapped == 0);
    check_contracts ();
    puts ("{\"stack_contracts\":\"passed\"}");
  }
  return 0;
}
