// Included first in every C++ compile of mvstexturing on MSVC, through /FI
// in External-MvsTexturing.cmake. It lets mapmap, which mvs-texturing
// downloads, compile its SIMD code with /arch:AVX2 on the path it takes
// with GCC for x86-64-v3. mapmap's sources are not changed.
#ifndef AIDM_MSVC_MAPMAP_H
#define AIDM_MSVC_MAPMAP_H

#include <cstring>
#include <immintrin.h>

// mapmap picks its 4 wide types with __SSE4_2__ and its 8 wide functions
// with __AVX__. GCC defines both for x86-64-v3. MSVC with /arch:AVX2 defines
// __AVX__ and __AVX2__ but not __SSE4_2__. AVX implies SSE4.2.
#if defined(_MSC_VER) && defined(__AVX__) && !defined(__SSE4_2__)
#define __SSE4_2__ 1
#endif

namespace aidm {

// Does what MASKMOVQ does: writes byte i of data to address[i] when the top
// bit of byte i of mask is set, and leaves the other bytes untouched.
inline void maskmove_si64(__m64 data, __m64 mask, char* address)
{
    unsigned char bytes[8];
    unsigned char select[8];
    std::memcpy(bytes, &data, sizeof(bytes));
    std::memcpy(select, &mask, sizeof(select));
    for (int i = 0; i < 8; ++i) {
        if (select[i] & 0x80) {
            address[i] = static_cast<char>(bytes[i]);
        }
    }
}

} // namespace aidm

// mapmap's 4 wide masked stores call the MMX intrinsic _mm_maskmove_si64,
// which MSVC declares for 32 bit x86 only.
#if defined(_MSC_VER) && defined(_M_X64)
#define _mm_maskmove_si64 aidm::maskmove_si64
#endif

#endif // AIDM_MSVC_MAPMAP_H
