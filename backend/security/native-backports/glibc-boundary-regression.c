/* Runtime boundary regressions for the exact glibc backport artifacts. */
#include <assert.h>
#include <errno.h>
#include <iconv.h>
#include <stdint.h>
#include <resolv.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static void check_resolver_search_case(int test_case)
{
    char long244[245], long256[257], search[280];
    memset(long244, 'a', sizeof(long244) - 1);
    memset(long256, 'a', sizeof(long256) - 1);
    long244[sizeof(long244) - 1] = '\0';
    long256[sizeof(long256) - 1] = '\0';
    for (size_t i = 63; i < 244; i += 64) {
        long244[i] = '.';
        long256[i] = '.';
    }
    if (test_case == 0)
        snprintf(search, sizeof(search), "example.com %s", long244);
    else if (test_case == 1)
        snprintf(search, sizeof(search), "%s example.com", long256);
    else
        snprintf(search, sizeof(search), "%s", long256);
    assert(setenv("LOCALDOMAIN", search, 1) == 0);
    assert(res_init() == 0);
    if (test_case == 0) {
        assert(_res.dnsrch[0] != NULL && strcmp(_res.dnsrch[0], "example.com") == 0);
        assert(_res.dnsrch[1] == NULL);
    } else {
        assert(_res.dnsrch[0] == NULL && _res.defdname[0] == '\0');
    }
}

static void check_resolver_search_truncation(void)
{
    /* Resolver configuration is cached. Each sample starts before initialization. */
    for (int test_case = 0; test_case < 3; test_case++) {
        pid_t child = fork();
        assert(child >= 0);
        if (child == 0) {
            check_resolver_search_case(test_case);
            _exit(0);
        }
        int status;
        assert(waitpid(child, &status, 0) == child);
        assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    }
}

static void check_conversion(const char *encoding, const unsigned char input[3])
{
    iconv_t converter = iconv_open("UTF-32LE", encoding);
    assert(converter != (iconv_t)-1);
    char *next = (char *)input;
    size_t remaining = 3;
    uint32_t points[4] = {0, 0, 0, 0};
    for (size_t i = 0; i < 4; i++) {
        char *output = (char *)&points[i];
        size_t available = sizeof(points[i]);
        errno = 0;
        size_t result = iconv(converter, &next, &remaining, &output, &available);
        if (i < 2) {
            assert(result == (size_t)-1 && errno == E2BIG);
        } else {
            assert(result == 0);
        }
        assert(remaining == (i < 2 ? 1 : 0));
        assert(available == (i == 3 ? sizeof(points[i]) : 0));
    }
    assert(points[0] == 0x304b && points[1] == 0x309a && points[2] == 0x41 && points[3] == 0);
    assert(iconv_close(converter) == 0);
}

int main(void)
{
    check_resolver_search_truncation();
    const unsigned char shift_jis[] = {0x82, 0xf5, 0x41};
    const unsigned char euc_jis[] = {0xa4, 0xf7, 0x41};
    check_conversion("SHIFT_JISX0213", shift_jis);
    check_conversion("EUC-JISX0213", euc_jis);
    const char *modes[] = {"r,ccs=", "r,ccs= ", "r,ccs=  "};
    for (size_t i = 0; i < sizeof(modes) / sizeof(modes[0]); i++) {
        errno = 0;
        FILE *stream = fopen("/dev/null", modes[i]);
        assert(stream == NULL && errno == EINVAL);
    }
    FILE *valid = fopen("/dev/null", "r,ccs=UTF-8");
    assert(valid != NULL);
    assert(fclose(valid) == 0);
    puts("Resolver truncation, JIS pending state and empty fopen charset regressions passed");
    return 0;
}
