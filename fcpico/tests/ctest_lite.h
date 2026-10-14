/*
 * ctest_lite.h -- minimal single-header test helper for fcpico/tests/fcbus.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * No test framework, no allocation: each test is a plain `main()` that calls CHECK*
 * macros and finishes with `return ctest_lite_result();`, which returns nonzero if any
 * check failed (registered with ctest's `add_test()` so `ctest --output-on-failure`
 * reports failures by exit code). Intended to be included by exactly one translation
 * unit (one test .c file), since it defines file-scope state.
 */
#ifndef CTEST_LITE_H
#define CTEST_LITE_H

#include <stdio.h>
#include <string.h>

static int ctest_lite_checks = 0;
static int ctest_lite_failures = 0;

/** Fails unless `cond` is true. */
#define CHECK(cond)                                                                     \
    do {                                                                                \
        ctest_lite_checks++;                                                            \
        if (!(cond)) {                                                                  \
            ctest_lite_failures++;                                                      \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond);    \
        }                                                                                \
    } while (0)

/** Fails unless integer/enum expressions `a` and `b` compare equal; prints both values. */
#define CHECK_EQ(a, b)                                                                   \
    do {                                                                                \
        ctest_lite_checks++;                                                            \
        long long ctest_lite_a_ = (long long)(a);                                       \
        long long ctest_lite_b_ = (long long)(b);                                       \
        if (ctest_lite_a_ != ctest_lite_b_) {                                           \
            ctest_lite_failures++;                                                      \
            fprintf(stderr, "%s:%d: CHECK_EQ failed: %s (%lld) != %s (%lld)\n",         \
                    __FILE__, __LINE__, #a, ctest_lite_a_, #b, ctest_lite_b_);           \
        }                                                                                \
    } while (0)

/** Fails unless the first `n` bytes of `a` and `b` are identical. */
#define CHECK_MEM(a, b, n)                                                               \
    do {                                                                                \
        ctest_lite_checks++;                                                            \
        size_t ctest_lite_n_ = (size_t)(n);                                             \
        if (memcmp((a), (b), ctest_lite_n_) != 0) {                                     \
            ctest_lite_failures++;                                                      \
            fprintf(stderr, "%s:%d: CHECK_MEM failed: %s vs %s (%zu bytes)\n",          \
                    __FILE__, __LINE__, #a, #b, ctest_lite_n_);                          \
        }                                                                                \
    } while (0)

/** Unconditional failure with a message; use for "should not get here" branches. */
#define FAIL(msg)                                                                        \
    do {                                                                                \
        ctest_lite_checks++;                                                            \
        ctest_lite_failures++;                                                          \
        fprintf(stderr, "%s:%d: FAIL: %s\n", __FILE__, __LINE__, (msg));                \
    } while (0)

/** Call once, as the last statement of main(): `return ctest_lite_result();`. */
static int ctest_lite_result(void) {
    if (ctest_lite_failures > 0) {
        fprintf(stderr, "FAILED: %d/%d checks failed\n", ctest_lite_failures, ctest_lite_checks);
        return 1;
    }
    fprintf(stderr, "OK: %d checks passed\n", ctest_lite_checks);
    return 0;
}

#endif /* CTEST_LITE_H */
