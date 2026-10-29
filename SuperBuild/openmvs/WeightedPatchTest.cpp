#include "WeightedPatch.h"

#include <algorithm>
#include <array>
#include <cfenv>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <random>
#include <vector>

struct PixelWeight {
    float weight;
    float tempWeight;
};

using Origin = std::array<float, 3>;
using Step = std::array<float, 9>;
using Weights = std::array<PixelWeight, 25>;
using Sums = std::array<float, 3>;

__attribute__((noinline, optimize("no-tree-vectorize")))
bool scalarPatch(const float* image, std::size_t stride, int width, int height,
                 const Origin& origin, const Step& step, const Weights& weights, Sums& sums)
{
    Origin rowStart = origin;
    sums = {};
    for (int row = 0; row < 5; ++row) {
        Origin current = rowStart;
        for (int column = 0; column < 5; ++column) {
            const float horizontal = current[0] / current[2];
            const float vertical = current[1] / current[2];
            if (!(horizontal >= 1.0f && vertical >= 1.0f
                  && horizontal <= float(width - 2) && vertical <= float(height - 2)))
                return false;
            const int left = int(horizontal);
            const int top = int(vertical);
            const float horizontalFraction = horizontal - left;
            const float verticalFraction = vertical - top;
            const float* firstRow = image + std::size_t(top) * stride + left;
            const float* secondRow = firstRow + stride;
            const float value = (firstRow[0] * (1.0f - horizontalFraction)
                               + firstRow[1] * horizontalFraction) * (1.0f - verticalFraction)
                              + (secondRow[0] * (1.0f - horizontalFraction)
                               + secondRow[1] * horizontalFraction) * verticalFraction;
            const PixelWeight& weight = weights[row * 5 + column];
            const float weightedValue = value * weight.weight;
            sums[0] += weightedValue;
            sums[1] += value * weightedValue;
            sums[2] += value * weight.tempWeight;
            current[0] += step[0];
            current[1] += step[3];
            current[2] += step[6];
        }
        rowStart[0] += step[1];
        rowStart[1] += step[4];
        rowStart[2] += step[7];
    }
    return true;
}

void check(bool condition, const char* message)
{
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(EXIT_FAILURE);
    }
}

bool compare(const std::vector<float>& image, std::size_t stride, const Origin& origin,
             const Step& step, const Weights& weights)
{
    Sums expected = {};
    Sums actual = {};
    const bool expectedInside = scalarPatch(image.data(), stride, 64, 64,
                                           origin, step, weights, expected);
    const bool actualInside = aidm::sampleWeightedPatch<5>(image.data(), stride, 64, 64,
        origin.data(), step.data(), weights.data(), actual[0], actual[1], actual[2]);
    check(expectedInside == actualInside, "Weighted patch border decision differs");
    if (actualInside && std::memcmp(expected.data(), actual.data(), sizeof(Sums)) != 0) {
        std::cerr << std::hexfloat << "Expected " << expected[0] << ' ' << expected[1]
                  << ' ' << expected[2] << ", got " << actual[0] << ' ' << actual[1]
                  << ' ' << actual[2] << '\n';
        check(false, "Weighted patch sums differ bit for bit");
    }
    return actualInside;
}

int main()
{
    std::mt19937 random(53);
    std::uniform_real_distribution<float> unit(0.0f, 1.0f);
    for (const std::size_t stride : {std::size_t(64), std::size_t(71)}) {
        std::vector<float> image(stride * 64, std::numeric_limits<float>::quiet_NaN());
        for (int row = 0; row < 64; ++row)
            for (int column = 0; column < 64; ++column)
                image[row * stride + column] = unit(random);
        Weights weights;
        for (PixelWeight& weight : weights) {
            weight.weight = unit(random);
            weight.tempWeight = unit(random) - 0.5f;
        }
        const Step identity = {1, 0, 0, 0, 1, 0, 0, 0, 1};
        check(compare(image, stride, {1, 1, 1}, identity, weights), "Lower border rejected");
        check(compare(image, stride, {58, 58, 1}, identity, weights), "Upper border rejected");
        check(!compare(image, stride, {0, 1, 1}, identity, weights), "Left border accepted");
        check(!compare(image, stride, {1, 0, 1}, identity, weights), "Top border accepted");
        check(!compare(image, stride, {59, 58, 1}, identity, weights), "Right border accepted");
        check(!compare(image, stride, {58, 59, 1}, identity, weights), "Bottom border accepted");
        check(compare(image, stride, {-10, -10, -1}, Step{}, weights), "Negative depth rejected");
        const Step pole = {-5, 0, 0, -5, 0, 0, -0.5f, 0, 0};
        check(!compare(image, stride, {10, 10, 1}, pole, weights), "Interior pole accepted");
        for (const float invalid : {std::numeric_limits<float>::infinity(),
                                   -std::numeric_limits<float>::infinity(),
                                   std::numeric_limits<float>::quiet_NaN()}) {
            check(!compare(image, stride, {invalid, 10, 1}, identity, weights),
                  "Non-finite horizontal coordinate accepted");
            check(!compare(image, stride, {10, invalid, 1}, identity, weights),
                  "Non-finite vertical coordinate accepted");
        }
        Sums unused = {};
        const Origin invalidOrigin = {0, 0, 0};
        check(!aidm::sampleWeightedPatch<5>(nullptr, stride, 64, 64, invalidOrigin.data(),
              identity.data(), weights.data(), unused[0], unused[1], unused[2]),
              "Zero depth accepted");
        for (const int rounding : {FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO}) {
            check(std::fesetround(rounding) == 0, "Cannot select rounding mode");
            for (int trial = 0; trial < 2000; ++trial) {
                const Origin origin = {4.0f + 46.0f * unit(random),
                                       4.0f + 46.0f * unit(random), 0.9f + 0.2f * unit(random)};
                const Step step = {2.0f * unit(random) - 0.5f, unit(random) - 0.5f, 0,
                                   unit(random) - 0.5f, 2.0f * unit(random) - 0.5f, 0,
                                   0.02f * unit(random) - 0.01f,
                                   0.02f * unit(random) - 0.01f, 0};
                compare(image, stride, origin, step, weights);
            }
        }
        check(std::fesetround(FE_TONEAREST) == 0, "Cannot restore rounding mode");
        std::fill(image.begin(), image.end(), 0.5f);
        check(compare(image, stride, {12.25f, 16.75f, 1}, identity, weights),
              "Constant patch rejected");
    }
    std::cout << "Weighted patch scalar equivalence passed\n";
    return EXIT_SUCCESS;
}
