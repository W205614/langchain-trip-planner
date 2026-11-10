/* Copyright (C) 2026 W205614.
   Downstream GNU C Library modification.
   SPDX-License-Identifier: LGPL-2.1-or-later

   This library is free software; you can redistribute it and/or modify it
   under the terms of the GNU Lesser General Public License as published by
   the Free Software Foundation; either version 2.1 of the License, or
   (at your option) any later version.
   This library is distributed in the hope that it will be useful, but
   WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
   or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public
   License for more details. */

#ifndef TRIP_NPTL_STACK_RANDOMIZATION_H
#define TRIP_NPTL_STACK_RANDOMIZATION_H
#if defined __linux__ && defined __x86_64__ && !defined __ILP32__
# include <sys/random.h>
# include <sysdep.h>

/* Use the original x86-64 low canonical range, including on LA57 kernels.
   Ordinary mmap never overwrites an occupied hint. Reject its alternate
   address, which could restore predictable kernel allocation. */
static void *
allocate_random_stack (size_t size, int prot, size_t pagesize)
{
  const uintptr_t lower = UINT64_C (1) << 32;
  const uintptr_t upper = UINT64_C (1) << 46;
  if (size > upper - lower)
    {
      __set_errno (ENOMEM);
      return MAP_FAILED;
    }
  uint64_t slots = (upper - lower - size) / pagesize + 1;
  uint64_t threshold = -slots % slots;
  for (unsigned int attempt = 0; attempt < 32; ++attempt)
    {
      uint64_t sample;
      /* pthread_create must not gain a cancellation point. An unavailable
         entropy source is an error, never a predictable fallback. */
      ssize_t count = INLINE_SYSCALL_CALL (getrandom, &sample, sizeof sample,
                                           GRND_NONBLOCK);
      if (count != (ssize_t) sizeof sample)
        {
          if (count >= 0)
            __set_errno (EIO);
          return MAP_FAILED;
        }
      if (sample < threshold)
        continue;
      void *hint = (void *) (lower + (sample % slots) * pagesize);
      void *mapping = __mmap (hint, size, prot,
                              MAP_PRIVATE | MAP_ANONYMOUS | MAP_STACK, -1, 0);
      if (mapping == MAP_FAILED)
        return MAP_FAILED;
      if (mapping == hint)
        return mapping;
      if (__munmap (mapping, size) != 0)
        return MAP_FAILED;
    }
  __set_errno (EAGAIN);
  return MAP_FAILED;
}
#endif
#endif
