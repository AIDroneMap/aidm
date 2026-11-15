// Checks MsvcMapmap.h. External-MvsTexturing.cmake compiles and runs it on
// MSVC before mvstexturing builds; tests/test_mvstexturing_msvc.py runs it
// with GCC, where _mm_maskmove_si64 is the real MASKMOVQ.
#include "MsvcMapmap.h"

#include <cstdint>
#include <cstdio>
#include <cstring>

#if defined(_MSC_VER) && defined(__AVX__) && !defined(__SSE4_2__)
#error "MsvcMapmap.h does not define __SSE4_2__ under /arch:AVX2"
#endif

namespace {

const unsigned char kGuard = 0xA5;

__m64 to_m64(std::uint64_t value)
{
    __m64 result;
    std::memcpy(&result, &value, sizeof(result));
    return result;
}

// The bytes MASKMOVQ writes, by its definition.
void reference(std::uint64_t data, std::uint64_t mask, unsigned char* out)
{
    for (int i = 0; i < 8; ++i) {
        if ((mask >> (8 * i + 7)) & 1) {
            out[i] = static_cast<unsigned char>(data >> (8 * i));
        }
    }
}

typedef void (*Store)(__m64, __m64, char*);

void real_intrinsic(__m64 data, __m64 mask, char* address)
{
    // On MSVC x64 this name is the macro of MsvcMapmap.h, as in mapmap.
    _mm_maskmove_si64(data, mask, address);
#if !defined(_MSC_VER)
    _mm_empty();
#endif
}

bool check(const char* name, Store store, std::uint64_t data, std::uint64_t mask)
{
    unsigned char got[24];
    unsigned char want[24];
    std::memset(got, kGuard, sizeof(got));
    std::memset(want, kGuard, sizeof(want));
    store(to_m64(data), to_m64(mask), reinterpret_cast<char*>(got + 8));
    reference(data, mask, want + 8);
    if (std::memcmp(got, want, sizeof(got)) != 0) {
        std::printf("%s: wrong bytes for data %016llx mask %016llx\n", name,
                    static_cast<unsigned long long>(data),
                    static_cast<unsigned long long>(mask));
        return false;
    }
    return true;
}

} // namespace

int main()
{
    const std::uint64_t masks[] = {
        0x0000000000000000ull, 0x8080808080808080ull, 0x7f7f7f7f7f7f7f7full,
        0xffffffffffffffffull, 0x8000000000000080ull, 0x0080008000800080ull,
    };
    std::uint64_t state = 0x9e3779b97f4a7c15ull;
    int cases = 0;
    for (int round = 0; round < 4096; ++round) {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        const std::uint64_t data = state;
        const std::uint64_t mask = round < 6 ? masks[round] : state * 0x2545f4914f6cdd1dull;
        if (!check("aidm::maskmove_si64", aidm::maskmove_si64, data, mask) ||
            !check("_mm_maskmove_si64", real_intrinsic, data, mask)) {
            return 1;
        }
        ++cases;
    }
    std::printf("maskmove: %d cases match MASKMOVQ\n", cases);
    return 0;
}
