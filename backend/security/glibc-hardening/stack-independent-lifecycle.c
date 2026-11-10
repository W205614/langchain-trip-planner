#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

enum { detached_count = 64 };
static atomic_int completed, stop_churn, cancellation_ready, allow_create, creation_returned;
static pthread_t cancellation_child;
static _Thread_local unsigned int marker;
static void *detached_bases[detached_count];
static void *noop(void *unused) { (void)unused; return NULL; }
static void probe(void)
{
  pthread_t thread;
  assert(pthread_create(&thread, NULL, noop, NULL) == 0);
  assert(pthread_join(thread, NULL) == 0);
}
static void *detached(void *argument)
{
  uintptr_t index = (uintptr_t)argument;
  pthread_attr_t attr;
  size_t size;
  assert(pthread_getattr_np(pthread_self(), &attr) == 0);
  assert(pthread_attr_getstack(&attr, &detached_bases[index], &size) == 0);
  assert(pthread_attr_destroy(&attr) == 0);
  marker = (unsigned int)index+123;
  for (int n=0; n<100; ++n) { sched_yield(); assert(marker == index+123); }
  atomic_fetch_add(&completed, 1);
  return NULL;
}
static void detached_stress(void)
{
  pthread_attr_t attr;
  assert(pthread_attr_init(&attr) == 0);
  assert(pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED) == 0);
  size_t page = (size_t)sysconf(_SC_PAGESIZE);
  for (int round=0; round<8; ++round) {
    atomic_store(&completed, 0);
    for (uintptr_t n=0; n<detached_count; ++n) {
      pthread_t id;
      assert(pthread_create(&id, &attr, detached, (void *)n) == 0);
      if (n % 4 == 0) probe();
    }
    while (atomic_load(&completed) != detached_count) sched_yield();
    int mapped = detached_count;
    for (int attempt=0; attempt<1000 && mapped; ++attempt) {
      probe(); /* Reap only entries whose kernel clear_child_tid completed. */
      mapped = 0;
      for (int n=0; n<detached_count; ++n) {
        unsigned char residency;
        errno = 0;
        int result = mincore(detached_bases[n], page, &residency);
        assert(result == 0 || (result == -1 && errno == ENOMEM));
        mapped += result == 0;
      }
      if (mapped) usleep(1000);
    }
    assert(mapped == 0);
  }
  assert(pthread_attr_destroy(&attr) == 0);
}
static void fork_probe(void)
{
  pid_t child = fork();
  assert(child >= 0);
  if (!child) { for (int n=0; n<8; ++n) probe(); _exit(0); }
  int status;
  assert(waitpid(child, &status, 0) == child);
  assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
}
static void *churn(void *arg)
{
  if (arg != NULL) fork_probe();
  while (!atomic_load(&stop_churn)) probe();
  return NULL;
}
static void active_fork_stress(void)
{
  pthread_t churners[4];
  for (int n=0; n<4; ++n)
    assert(pthread_create(&churners[n], NULL, churn, (void *)(uintptr_t)(n==0)) == 0);
  for (int n=0; n<20; ++n) fork_probe();
  atomic_store(&stop_churn, 1);
  for (int n=0; n<4; ++n) assert(pthread_join(churners[n], NULL) == 0);
}
static void *pending_cancel(void *unused)
{
  (void)unused;
  atomic_store(&cancellation_ready, 1);
  while (!atomic_load(&allow_create)) sched_yield();
  assert(pthread_create(&cancellation_child, NULL, noop, NULL) == 0);
  atomic_store(&creation_returned, 1);
  pthread_testcancel();
  abort();
}
static void cancellation_contract(void)
{
  pthread_t worker;
  assert(pthread_create(&worker, NULL, pending_cancel, NULL) == 0);
  while (!atomic_load(&cancellation_ready)) sched_yield();
  assert(pthread_cancel(worker) == 0);
  atomic_store(&allow_create, 1);
  void *result;
  assert(pthread_join(worker, &result) == 0 && result == PTHREAD_CANCELED);
  assert(atomic_load(&creation_returned) == 1);
  assert(pthread_join(cancellation_child, NULL) == 0);
}
int main(void)
{
  alarm(45);
  detached_stress();
  puts("{\"detached_512_threads_with_reclamation\":\"passed\"}");
  active_fork_stress();
  puts("{\"fork_with_active_stack_churn_and_worker_fork\":\"passed\"}");
  cancellation_contract();
  puts("{\"deferred_pending_cancel_pthread_create\":\"passed\"}");
  return 0;
}
