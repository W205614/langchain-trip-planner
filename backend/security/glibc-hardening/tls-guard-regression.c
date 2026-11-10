/* Real TLS-guard, cache-resize and pthread attribute regression.
   Derived from the upstream 2026 TLS-guard RFC and support_mem_access.c.
   Copyright (C) 2025-2026 Free Software Foundation, Inc.

   This file is free software; you can redistribute it and/or modify it
   under the terms of the GNU Lesser General Public License as published by
   the Free Software Foundation; either version 2.1, or (at your option)
   any later version.  This file is distributed WITHOUT ANY WARRANTY;
   without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
   PARTICULAR PURPOSE.  See <https://www.gnu.org/licenses/>.  */

#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <setjmp.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#if !defined __x86_64__ || !defined __LP64__
# error "This downstream TLS-guard regression targets x86_64 LP64."
#endif

#define CHECK(test) do { if (!(test)) { \
  fprintf (stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #test); \
  exit (1); } } while (0)

static size_t pagesize;
static __thread unsigned char tls_probe[8192] __attribute__ ((aligned (64)));
static sigjmp_buf access_jump;

static void
access_fault (int signo)
{
  siglongjmp (access_jump, signo);
}

/* Upstream's real read/write fault probe.  Calls are serial, including
   across worker threads; this is intentionally not a concurrent helper.  */
static bool
memory_access (const void *address, bool write)
{
  static bool installed;
  if (!installed)
    {
      struct sigaction action = { .sa_handler = access_fault,
                                  .sa_flags = SA_NODEFER };
      sigemptyset (&action.sa_mask);
      CHECK (sigaction (SIGSEGV, &action, NULL) == 0);
      CHECK (sigaction (SIGBUS, &action, NULL) == 0);
      installed = true;
    }
  int result = sigsetjmp (access_jump, 0);
  if (result == 0)
    {
      if (write)
        *(volatile unsigned char *) address = 'x';
      else
        (void) *(volatile const unsigned char *) address;
      return true;
    }
  CHECK (result == SIGSEGV || result == SIGBUS);
  return false;
}

struct test_case
{
  const char *name;
  size_t requested_size;
  size_t requested_guard;
  bool user_stack;
  void *allocation;
  pthread_t identity;
};

static void *
worker (void *closure)
{
  struct test_case *test = closure;
  for (size_t index = 0; index < sizeof tls_probe; index++)
    CHECK (tls_probe[index] == 0);
  memset (tls_probe, 0x5a, sizeof tls_probe);
  pthread_attr_t attributes;
  CHECK (pthread_getattr_np (pthread_self (), &attributes) == 0);
  void *base;
  size_t size, guard;
  CHECK (pthread_attr_getstack (&attributes, &base, &size) == 0);
  CHECK (pthread_attr_getguardsize (&attributes, &guard) == 0);
  CHECK (pthread_attr_destroy (&attributes) == 0);
  size_t expected_guard = test->user_stack ? 0
    : (test->requested_guard + pagesize - 1) & ~(pagesize - 1);
  CHECK (guard == expected_guard);
  CHECK (size >= test->requested_size);
  CHECK (memory_access (base, false));
  CHECK (memory_access (base, true));
  CHECK (memory_access ((char *) base + size / 2, false));

  if (guard != 0)
    {
      CHECK (!memory_access ((char *) base - 1, false));
      CHECK (!memory_access ((char *) base - guard, true));
    }

  /* Start strictly below a known live static-TLS object, not above the
     mapping's high end.  A gap above the allocation cannot masquerade
     as the new guard.  Stop above the current stack frame.  */
  unsigned char stack_local;
  uintptr_t top = (uintptr_t) tls_probe & ~(pagesize - 1);
  uintptr_t limit = (uintptr_t) &stack_local & ~(pagesize - 1);
  CHECK (top >= limit);
  uintptr_t start = 0, end = 0;
  for (uintptr_t page = top; page > limit; )
    {
      page -= pagesize;
      if (!memory_access ((void *) page, false))
        {
          if (end == 0)
            end = page + pagesize;
          start = page;
        }
      else if (end != 0)
        break;
    }
  if (guard != 0)
    {
      CHECK (end != 0 && start > (uintptr_t) &stack_local);
      CHECK (end <= (uintptr_t) tls_probe);
      CHECK (end - start == guard);
      CHECK (!memory_access ((void *) start, false));
      CHECK (!memory_access ((void *) start, true));
      CHECK (!memory_access ((void *) (end - 1), false));
      CHECK (!memory_access ((void *) (end - 1), true));
      CHECK (memory_access ((void *) (start - 1), false));
      CHECK (memory_access ((void *) end, false));
    }
  else
    CHECK (end == 0);

  for (size_t index = 0; index < sizeof tls_probe; index++)
    CHECK (tls_probe[index] == 0x5a);
  test->allocation = (char *) base - guard;
  test->identity = pthread_self ();
  printf ("PASS %s: reported size=%zu guard=%zu, TLS gap=%zu\n",
          test->name, size, guard, (size_t) (end - start));
  return NULL;
}

static void
run_case (struct test_case *test)
{
  pthread_attr_t attributes;
  CHECK (pthread_attr_init (&attributes) == 0);
  CHECK (pthread_attr_setguardsize (&attributes, test->requested_guard) == 0);
  void *user_memory = NULL;
  if (test->user_stack)
    {
      user_memory = mmap (NULL, test->requested_size, PROT_READ | PROT_WRITE,
                          MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
      CHECK (user_memory != MAP_FAILED);
      CHECK (pthread_attr_setstack (&attributes, user_memory,
                                    test->requested_size) == 0);
    }
  else
    CHECK (pthread_attr_setstacksize (&attributes, test->requested_size) == 0);
  pthread_t thread;
  CHECK (pthread_create (&thread, &attributes, worker, test) == 0);
  void *result;
  CHECK (pthread_join (thread, &result) == 0 && result == NULL);
  CHECK (pthread_attr_destroy (&attributes) == 0);
  if (user_memory != NULL)
    CHECK (munmap (user_memory, test->requested_size) == 0);
}

int
main (int argc, char **argv)
{
  bool require_reuse = argc == 2
    && strcmp (argv[1], "--require-cache-reuse") == 0;
  CHECK (argc == 1 || require_reuse);
  long page = sysconf (_SC_PAGESIZE);
  CHECK (page > 0 && ((size_t) page & ((size_t) page - 1)) == 0);
  pagesize = page;
  const size_t size = 2 * 1024 * 1024;
  /* Hold total requested physical size S+2G constant.  On the TLS-only
     build this forces exact cache reuse while shrinking, removing and
     expanding both guards.  The final no-cache hardening can run without
     --require-cache-reuse and must not claim that path was exercised.  */
  struct test_case cases[] = {
    {"cache large guard", size, 8 * pagesize, false, NULL, 0},
    {"cache shrink guard", size + 14 * pagesize, pagesize, false, NULL, 0},
    {"cache remove guard", size + 16 * pagesize, 0, false, NULL, 0},
    {"cache expand guard", size + 12 * pagesize, 2 * pagesize, false, NULL, 0},
    {"unaligned guard", size, pagesize + 1, false, NULL, 0},
    {"no guard", size, 0, false, NULL, 0},
    {"user supplied stack ignores guard", size, 4 * pagesize, true, NULL, 0},
  };
  for (size_t index = 0; index < sizeof cases / sizeof cases[0]; index++)
    {
      run_case (&cases[index]);
      if (require_reuse && index > 0 && index < 4)
        {
          CHECK (cases[index].allocation == cases[0].allocation);
          CHECK (pthread_equal (cases[index].identity, cases[0].identity));
        }
    }
  pthread_attr_t attributes;
  CHECK (pthread_attr_init (&attributes) == 0);
  CHECK (pthread_attr_setguardsize (&attributes, SIZE_MAX) == 0);
  pthread_t invalid;
  CHECK (pthread_create (&invalid, &attributes, worker, NULL) == EINVAL);
  CHECK (pthread_attr_destroy (&attributes) == 0);
  puts ("PASS guard overflow rejected; real read/write and TLS values checked");
  return 0;
}
