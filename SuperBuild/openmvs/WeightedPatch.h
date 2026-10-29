#pragma once

#include <cstddef>

namespace aidm {

template <int PatchWidth, typename PixelWeight>
bool sampleWeightedPatch(const float* image, std::ptrdiff_t stride, int width, int height,
                         const float* origin, const float* step,
                         const PixelWeight* weights, float& sum, float& sumSq, float& numerator)
{
    constexpr int sampleCount = PatchWidth * PatchWidth;
    float horizontal[sampleCount];
    float vertical[sampleCount];
    float depth[sampleCount];
    float samples[sampleCount];
    float upperLeft[sampleCount];
    float upperRight[sampleCount];
    float lowerLeft[sampleCount];
    float lowerRight[sampleCount];
    float rowHorizontal = origin[0];
    float rowVertical = origin[1];
    float rowDepth = origin[2];
    for (int row = 0; row < PatchWidth; ++row) {
        float currentHorizontal = rowHorizontal;
        float currentVertical = rowVertical;
        float currentDepth = rowDepth;
        for (int column = 0; column < PatchWidth; ++column) {
            const int index = row * PatchWidth + column;
            horizontal[index] = currentHorizontal;
            vertical[index] = currentVertical;
            depth[index] = currentDepth;
            currentHorizontal += step[0];
            currentVertical += step[3];
            currentDepth += step[6];
        }
        rowHorizontal += step[1];
        rowVertical += step[4];
        rowDepth += step[7];
    }

    int inside = 1;
    #pragma omp simd reduction(&:inside)
    for (int index = 0; index < sampleCount; ++index) {
        horizontal[index] /= depth[index];
        vertical[index] /= depth[index];
        inside &= (horizontal[index] >= 1.0f) & (vertical[index] >= 1.0f)
               & (horizontal[index] <= float(width - 2))
               & (vertical[index] <= float(height - 2));
    }
    if (!inside)
        return false;

    for (int index = 0; index < sampleCount; ++index) {
        const int column = int(horizontal[index]);
        const int row = int(vertical[index]);
        horizontal[index] -= column;
        vertical[index] -= row;
        const float* upper = image + std::ptrdiff_t(row) * stride + column;
        const float* lower = upper + stride;
        upperLeft[index] = upper[0];
        upperRight[index] = upper[1];
        lowerLeft[index] = lower[0];
        lowerRight[index] = lower[1];
    }

    #pragma omp simd
    for (int index = 0; index < sampleCount; ++index) {
        const float fractionHorizontal = horizontal[index];
        const float fractionVertical = vertical[index];
        const float remainingHorizontal = 1.0f - fractionHorizontal;
        const float remainingVertical = 1.0f - fractionVertical;
        samples[index] = (upperLeft[index] * remainingHorizontal
                       + upperRight[index] * fractionHorizontal)
                       * remainingVertical
                       + (lowerLeft[index] * remainingHorizontal
                       + lowerRight[index] * fractionHorizontal)
                       * fractionVertical;
    }

    sum = 0.0f;
    sumSq = 0.0f;
    numerator = 0.0f;
    for (int index = 0; index < sampleCount; ++index) {
        const float value = samples[index];
        const float weightedValue = value * weights[index].weight;
        sum += weightedValue;
        sumSq += value * weightedValue;
        numerator += value * weights[index].tempWeight;
    }
    return true;
}

}
