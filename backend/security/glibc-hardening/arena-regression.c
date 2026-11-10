/* Downstream Linux x86_64 allocator regression, LGPL-2.1-or-later.
   Exercises real glibc allocation/free and observes kernel VMAs, rather than
   copying heap_for_ptr's arithmetic into the test. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <malloc.h>
#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

enum { THREADS = 4, SLOTS = 1152 };
static unsigned char *slots[THREADS][SLOTS];
static size_t sizes[THREADS][SLOTS];
static pthread_barrier_t ready, release_workers;

static void
barrier (pthread_barrier_t *value)
{
  int ret = pthread_barrier_wait (value);
  assert (ret == 0 || ret == PTHREAD_BARRIER_SERIAL_THREAD);
}

static unsigned char
marker (size_t owner, size_t index)
{
  return (unsigned char) (owner * 31 + index + 1);
}

static void
release_row (size_t owner)
{
  for (size_t index = 0; index < SLOTS; ++index)
    {
      unsigned char *p = slots[owner][index];
      assert (p[0] == marker (owner, index));
      assert (p[sizes[owner][index] - 1] == marker (owner, index));
      free (p);
    }
}

static void *
worker (void *argument)
{
  size_t owner = (uintptr_t) argument;
  for (size_t index = 0; index < SLOTS; ++index)
    {
      size_t size = 65536 + (index % 5) * 16;
      unsigned char *p;
      if (index % 97 == 0)
        {
          void *aligned;
          assert (posix_memalign (&aligned, 65536, size) == 0);
          p = aligned;
          assert ((uintptr_t) p % 65536 == 0);
        }
      else if (index % 31 == 0)
        {
          p = calloc (1, size);
          assert (p != NULL && p[0] == 0 && p[size - 1] == 0);
        }
      else
        p = malloc (size);
      assert (p != NULL && malloc_usable_size (p) >= size);
      memset (p, marker (owner, index), 32);
      memset (p + size - 32, marker (owner, index), 32);
      if (index % 71 == 0)
        {
          p = realloc (p, size + 4096);
          assert (p != NULL && p[0] == marker (owner, index));
          assert (p[size - 1] == marker (owner, index));
          size += 4096;
          memset (p + size - 32, marker (owner, index), 32);
        }
      sizes[owner][index] = size;
      slots[owner][index] = p;
    }
  barrier (&ready);
  barrier (&release_workers);
  release_row ((owner + 1) % THREADS);
  return NULL;
}

static uintptr_t
mapping_start (const void *pointer)
{
  FILE *maps = fopen ("/proc/self/maps", "r");
  assert (maps != NULL);
  char line[512];
  uintptr_t result = 0;
  while (fgets (line, sizeof (line), maps) != NULL)
    {
      unsigned long start, end;
      if (sscanf (line, "%lx-%lx", &start, &end) == 2
          && (uintptr_t) pointer >= start && (uintptr_t) pointer < end)
        {
          assert (strstr (line, "[heap]") == NULL);
          result = start;
          break;
        }
    }
  assert (fclose (maps) == 0 && result != 0);
  return result;
}

static void *
child_worker (void *unused)
{
  (void) unused;
  for (size_t n = 1; n <= 1000; ++n)
    {
      void *p = malloc (n * 101);
      assert (p != NULL);
      memset (p, 0xa5, n * 101);
      free (p);
    }
  return NULL;
}

/* Invoke before the first explicit allocation; do not prime malloc here.
   The old libc accepts denied entropy through its tcache fallback. The new
   arena initialization must fail closed with its distinct fatal message. */
static int
block_entropy (void)
{
  struct sock_filter instructions[] = {
    BPF_STMT (BPF_LD | BPF_W | BPF_ABS, offsetof (struct seccomp_data, arch)),
    BPF_JUMP (BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
    BPF_STMT (BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
    BPF_STMT (BPF_LD | BPF_W | BPF_ABS, offsetof (struct seccomp_data, nr)),
    BPF_JUMP (BPF_JMP | BPF_JEQ | BPF_K, SYS_getrandom, 0, 1),
    BPF_STMT (BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
    BPF_STMT (BPF_RET | BPF_K, SECCOMP_RET_ALLOW)
  };
  struct sock_fprog filter = {
    .len = sizeof (instructions) / sizeof (instructions[0]),
    .filter = instructions
  };
  assert (prctl (PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0);
  assert (prctl (PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &filter) == 0);
  unsigned char *p = malloc (64);
  assert (p != NULL);
  p[0] = 1;
  assert (write (STDOUT_FILENO, p, 1) == 1);
  free (p);
  return 0;
}

int
main (int argc, char **argv)
{
  if (argc == 2 && strcmp (argv[1], "--block-entropy") == 0)
    return block_entropy ();
  assert (mallopt (M_MMAP_MAX, 0) == 1);
  assert (mallopt (M_ARENA_MAX, THREADS + 2) == 1);
  assert (pthread_barrier_init (&ready, NULL, THREADS + 1) == 0);
  assert (pthread_barrier_init (&release_workers, NULL, THREADS + 1) == 0);
  pthread_t threads[THREADS];
  for (size_t index = 0; index < THREADS; ++index)
    assert (pthread_create (&threads[index], NULL, worker, (void *) index) == 0);
  barrier (&ready);
  uintptr_t starts[THREADS];
  for (size_t index = 0; index < THREADS; ++index)
    starts[index] = mapping_start (slots[index][0]);
  pid_t child = fork ();
  assert (child >= 0);
  if (child == 0)
    {
      for (size_t index = 0; index < THREADS; ++index)
        release_row (index);
      (void) malloc_trim (0);
      pthread_t thread;
      assert (pthread_create (&thread, NULL, child_worker, NULL) == 0);
      assert (pthread_join (thread, NULL) == 0);
      _exit (0);
    }
  int status;
  assert (waitpid (child, &status, 0) == child);
  assert (WIFEXITED (status) && WEXITSTATUS (status) == 0);
  barrier (&release_workers);
  for (size_t index = 0; index < THREADS; ++index)
    assert (pthread_join (threads[index], NULL) == 0);
  (void) malloc_trim (0);
  assert (pthread_barrier_destroy (&ready) == 0);
  assert (pthread_barrier_destroy (&release_workers) == 0);
  printf ("{\"threads\":%d,\"slots_per_thread\":%d,\"cross_thread_free\":true,"
          "\"fork_and_child_thread\":true,\"heap_mapping_starts\":[", THREADS, SLOTS);
  for (size_t index = 0; index < THREADS; ++index)
    printf ("%s%lu", index == 0 ? "" : ",", (unsigned long) starts[index]);
  puts ("]}");
  return 0;
}
