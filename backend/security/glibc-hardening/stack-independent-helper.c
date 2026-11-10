#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <sys/random.h>
#include <unistd.h>

static uint64_t samples[40];
static unsigned int reads, maps, unmaps, supplied;
static ssize_t entropy_result = 8;
static int map_error;
static void *alternates[40];
static size_t request_size;
static ssize_t test_getrandom(void *buffer, size_t length, unsigned int flags)
{
  assert(length == 8 && flags == GRND_NONBLOCK);
  if (entropy_result != 8) {
    ++reads;
    errno = EAGAIN;
    return entropy_result;
  }
  assert(reads < supplied);
  *(uint64_t *)buffer = samples[reads++];
  return 8;
}
static void *test_mmap(void *hint, size_t size, int prot, int flags, int fd, off_t offset)
{
  ++maps;
  if (map_error) { errno = map_error; return MAP_FAILED; }
  void *result = mmap(hint, size, prot, flags, fd, offset);
  if (result != MAP_FAILED && result != hint)
    alternates[maps-1] = result;
  request_size = size;
  return result;
}
static int test_munmap(void *mapping, size_t size)
{
  ++unmaps;
  return munmap(mapping, size);
}
#define INLINE_SYSCALL_CALL(name, ...) test_##name(__VA_ARGS__)
#define __set_errno(value) (errno = (value))
#define __mmap test_mmap
#define __munmap test_munmap
#include "stack-randomization.h"
#undef __mmap
#undef __munmap

static void reset(void)
{
  reads = maps = unmaps = supplied = 0;
  entropy_result = 8;
  map_error = 0;
  for (unsigned int n=0; n<40; ++n) alternates[n] = NULL;
}
static uint64_t sample_for(size_t size, uint64_t residue, size_t page)
{
  uint64_t slots = (((UINT64_C(1)<<46)-(UINT64_C(1)<<32))-size)/page+1;
  return (UINT64_MAX/slots-1)*slots+residue;
}
static void verify_alternates_unmapped(size_t page)
{
  for (unsigned int n=0; n<maps; ++n)
    if (alternates[n]) {
      unsigned char residency;
      assert(mincore(alternates[n], page, &residency) == -1 && errno == ENOMEM);
    }
}
int main(void)
{
  size_t page = (size_t)sysconf(_SC_PAGESIZE);
  void *lower = (void *)(UINT64_C(1)<<32);
  void *occupied = mmap(lower, page, PROT_READ|PROT_WRITE,
                        MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE, -1, 0);
  assert(occupied == lower);
  *(volatile unsigned char *)occupied = 0x5a;

  /* Exercise the actual helper with real mmap collision and real unmap. */
  reset();
  size_t size = page+17;
  samples[0] = sample_for(size, 0, page);
  samples[1] = sample_for(size, 1, page);
  supplied = 2;
  void *result = allocate_random_stack(size, PROT_READ|PROT_WRITE, page);
  assert(result == (char *)lower+page && reads == 2 && maps == 2 && unmaps == 1);
  assert(request_size == size && *(volatile unsigned char *)occupied == 0x5a);
  verify_alternates_unmapped(page);
  assert(munmap(result, size) == 0);

  reset();
  for (unsigned int n=0; n<32; ++n) samples[n] = sample_for(page, 0, page);
  supplied = 32;
  assert(allocate_random_stack(page, PROT_READ|PROT_WRITE, page) == MAP_FAILED);
  assert(errno == EAGAIN && reads == 32 && maps == 32 && unmaps == 32);
  assert(*(volatile unsigned char *)occupied == 0x5a);
  verify_alternates_unmapped(page);

  reset();
  uint64_t slots = (((UINT64_C(1)<<46)-(UINT64_C(1)<<32))-page)/page+1;
  assert(-slots % slots != 0);
  samples[0] = 0; /* Rejected by unbiased rejection sampling. */
  samples[1] = sample_for(page, 1, page);
  supplied = 2;
  result = allocate_random_stack(page, PROT_READ|PROT_WRITE, page);
  assert(result == (char *)lower+page && reads == 2 && maps == 1 && unmaps == 0);
  assert(munmap(result, page) == 0);

  reset();
  assert(allocate_random_stack(SIZE_MAX, PROT_NONE, page) == MAP_FAILED);
  assert(errno == ENOMEM && reads == 0 && maps == 0);
  assert(allocate_random_stack((UINT64_C(1)<<46)-(UINT64_C(1)<<32)+1,
                                PROT_NONE, page) == MAP_FAILED);
  assert(errno == ENOMEM && reads == 0 && maps == 0);
  reset(); entropy_result = 4;
  assert(allocate_random_stack(page, PROT_NONE, page) == MAP_FAILED);
  assert(errno == EIO && reads == 1 && maps == 0);
  reset(); entropy_result = -1;
  assert(allocate_random_stack(page, PROT_NONE, page) == MAP_FAILED);
  assert(errno == EAGAIN && reads == 1 && maps == 0);
  reset(); map_error = ENOMEM; supplied = 1; samples[0] = sample_for(page, 1, page);
  assert(allocate_random_stack(page, PROT_NONE, page) == MAP_FAILED);
  assert(errno == ENOMEM && reads == 1 && maps == 1 && unmaps == 0);
  assert(munmap(occupied, page) == 0);
  puts("{\"helper_actual_header\":\"passed\",\"cases\":[\"occupied_hint_retry\",\"unaligned_size\",\"32_attempt_exhaustion\",\"alternate_mapping_reclamation\",\"rejection_threshold\",\"size_overflow\",\"short_entropy\",\"entropy_error\",\"mmap_error\"]}");
}
