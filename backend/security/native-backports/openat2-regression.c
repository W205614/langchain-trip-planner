/* Exercise the actual util-linux helper without mount privileges. */
#include "fileutils.h"
#include "mountP.h"
#include <assert.h>
#include <errno.h>
#include <stdlib.h>

int main(void)
{
    char directory[] = "/tmp/trip-openat2-XXXXXX";
    assert(mkdtemp(directory));
    int root = open(directory, O_PATH | O_DIRECTORY | O_CLOEXEC);
    assert(root >= 0);
    int file = openat(root, "file", O_CREAT | O_RDWR | O_CLOEXEC, 0600);
    assert(file >= 0);
    close(file);
    assert(symlinkat("file", root, "link") == 0);
    assert(mkdirat(root, "inside", 0700) == 0);
    assert(symlinkat("inside", root, "directory-link") == 0);

    file = ul_openat_resolve(root, "file", O_PATH | O_CLOEXEC, 0, RESOLVE_NO_SYMLINKS);
    assert(file >= 0);
    close(file);
    errno = 0;
    assert(ul_openat_resolve(root, "link", O_PATH | O_CLOEXEC, 0, RESOLVE_NO_SYMLINKS) == -1);
    assert(errno == ELOOP);
    errno = 0;
    assert(ul_openat_resolve(root, "directory-link/..", O_PATH | O_CLOEXEC, 0, RESOLVE_NO_SYMLINKS) == -1);
    assert(errno == ELOOP);
    errno = 0;
    assert(ul_openat_resolve(root, "..", O_PATH | O_CLOEXEC, 0, RESOLVE_BENEATH) == -1);
    assert(errno == EXDEV);

    /* Check the actual libmount caller's flag, not only the helper's argument. */
    char path[sizeof directory + 32];
    assert(snprintf(path, sizeof path, "%s/directory-link/..", directory) > 0);
    struct libmnt_context *context = mnt_new_context();
    assert(context);
    context->restricted = 1;
    assert(mnt_context_open_tree(context, path, 0) == -1);
    assert(mnt_context_get_syscall_errno(context) == ELOOP);
    mnt_free_context(context);

    assert(unlinkat(root, "link", 0) == 0);
    assert(unlinkat(root, "directory-link", 0) == 0);
    assert(unlinkat(root, "file", 0) == 0);
    assert(unlinkat(root, "inside", AT_REMOVEDIR) == 0);
    close(root);
    assert(rmdir(directory) == 0);
    return 0;
}
