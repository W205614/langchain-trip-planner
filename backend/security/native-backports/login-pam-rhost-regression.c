/* Test-only observers for the real login binary before authentication starts. */
#define _GNU_SOURCE
#include <errno.h>
#include <netdb.h>
#include <security/pam_appl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int gethostname(char *buffer, size_t length)
{
    const char hostname[] = "local.example.test";
    if (length < sizeof(hostname)) {
        errno = ENAMETOOLONG;
        return -1;
    }
    memcpy(buffer, hostname, sizeof(hostname));
    return 0;
}

int getaddrinfo(const char *node, const char *service,
                const struct addrinfo *hints, struct addrinfo **result)
{
    (void)node;
    (void)service;
    (void)hints;
    *result = NULL;
    return EAI_NONAME;
}

int pam_start(const char *service, const char *user,
              const struct pam_conv *conversation, pam_handle_t **handle)
{
    (void)service;
    (void)user;
    (void)conversation;
    *handle = (pam_handle_t *)1;
    return PAM_SUCCESS;
}

int pam_set_item(pam_handle_t *handle, int item, const void *value)
{
    (void)handle;
    if (item == PAM_RHOST) {
        const char *expected = getenv("TRIP_LOGIN_EXPECT_RHOST");
        if (expected != NULL && value != NULL && strcmp(value, expected) == 0)
            _exit(0);
        _exit(95);
    }
    return PAM_SUCCESS;
}
