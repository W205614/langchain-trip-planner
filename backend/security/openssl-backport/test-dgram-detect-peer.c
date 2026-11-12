#define _GNU_SOURCE
#include <arpa/inet.h>
#include <openssl/bio.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static int detect_peer(int family)
{
    int receiver = -1, peer = -1, ok = 0;
    struct sockaddr_storage local = { 0 }, detected = { 0 };
    socklen_t local_len = sizeof(local);
    BIO *bio = NULL;
    long detected_len;

    receiver = socket(family, SOCK_DGRAM, 0);
    peer = socket(family, SOCK_DGRAM, 0);
    if (receiver < 0 || peer < 0)
        goto done;
    if (family == AF_INET6) {
        struct sockaddr_in6 address = { .sin6_family = AF_INET6,
                                        .sin6_port = htons(443) };
        if (inet_pton(AF_INET6, "::ffff:127.0.0.2", &address.sin6_addr) != 1)
            goto done;
        memcpy(&local, &address, sizeof(address));
        local_len = sizeof(address);
    } else {
        struct sockaddr_in address = { .sin_family = AF_INET,
                                       .sin_addr.s_addr = htonl(INADDR_LOOPBACK) };
        if (bind(receiver, (struct sockaddr *)&address, sizeof(address)) != 0
            || getsockname(receiver, (struct sockaddr *)&local, &local_len) != 0)
            goto done;
    }
    /* Keep BIO's peer unset so DETECT_PEER_ADDR must call getpeername. */
    bio = BIO_new_dgram(peer, BIO_NOCLOSE);
    if (bio == NULL)
        goto done;
    if (connect(peer, (struct sockaddr *)&local, local_len) != 0)
        goto done;
    detected_len = BIO_ctrl(bio, BIO_CTRL_DGRAM_DETECT_PEER_ADDR,
                            sizeof(detected), &detected);
    if ((family == AF_INET6
            && (detected_len != sizeof(struct sockaddr_in6)
                || ((struct sockaddr *)&detected)->sa_family != AF_INET6
                || memcmp(&((struct sockaddr_in6 *)&detected)->sin6_addr,
                          &((struct sockaddr_in6 *)&local)->sin6_addr,
                          sizeof(struct in6_addr)) != 0))
        || (family == AF_INET
            && (detected_len != sizeof(struct sockaddr_in)
                || ((struct sockaddr *)&detected)->sa_family != AF_INET
                || ((struct sockaddr_in *)&detected)->sin_addr.s_addr
                    != ((struct sockaddr_in *)&local)->sin_addr.s_addr)))
        goto done;
    ok = 1;
done:
    BIO_free(bio);
    if (peer >= 0)
        close(peer);
    if (receiver >= 0)
        close(receiver);
    return ok;
}

int main(void)
{
    if (!detect_peer(AF_INET)) {
        fputs("IPv4 BIO datagram peer detection failed\n", stderr);
        return 1;
    }
    if (!detect_peer(AF_INET6)) {
        fputs("IPv6 BIO datagram peer detection failed\n", stderr);
        return 1;
    }
    return 0;
}
