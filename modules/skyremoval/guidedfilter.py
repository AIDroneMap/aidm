import numpy as np

# Based on Fast Guided Filter
# Kaiming He, Jian Sun
# https://arxiv.org/abs/1505.00996

def box(img, radius, sum_dtype=None):
    """Sum of img over a (2 * radius + 1) square window, clipped at the borders.

    The result has the dtype of img. sum_dtype is the dtype of the running
    sums; it defaults to the dtype of img.
    """
    dst = np.zeros_like(img)
    (r, c) = img.shape

    s = [radius, 1]
    c_sum = np.cumsum(img, 0, dtype=sum_dtype)
    dst[0:radius+1, :, ...] = c_sum[radius:2*radius+1, :, ...]
    np.subtract(c_sum[2*radius+1:r, :, ...], c_sum[0:r-2*radius-1, :, ...], out=dst[radius+1:r-radius, :, ...])
    dst[r-radius:r, :, ...] = np.tile(c_sum[r-1:r, :, ...], s) - c_sum[r-2*radius-1:r-radius-1, :, ...]
    del c_sum

    s = [1, radius]
    c_sum = np.cumsum(dst, 1, dtype=sum_dtype)
    dst[:, 0:radius+1, ...] = c_sum[:, radius:2*radius+1, ...]
    np.subtract(c_sum[:, 2*radius+1 : c, ...], c_sum[:, 0 : c-2*radius-1, ...], out=dst[:, radius+1:c-radius, ...])
    dst[:, c-radius: c, ...] = np.tile(c_sum[:, c-1:c, ...], s) - c_sum[:, c-2*radius-1 : c-radius-1, ...]

    return dst


def guided_filter(img, guide, radius, eps):
    """Guided filter of guide, with img as the guidance image.

    img and guide are 2D arrays of the same dtype, float32 in AIDM, and so is
    the result. The steps run in place where possible, so that about ten
    full-size arrays are alive at the peak. The box sums of a and b run in
    float64. In float32 they flip about 100 times more mask pixels at the
    threshold.
    """
    (r, c) = img.shape

    CNT = box(np.ones([r, c], dtype=img.dtype), radius)

    mean_img = box(img, radius)
    mean_img /= CNT
    mean_guide = box(guide, radius)
    mean_guide /= CNT

    # a = cov(img, guide) / (var(img) + eps)
    a = box(img * guide, radius)
    a /= CNT
    a -= mean_img * mean_guide
    var = box(img * img, radius)
    var /= CNT
    var -= mean_img * mean_img
    var += eps
    a /= var
    del var

    # b = mean_guide - a * mean_img
    b = mean_guide
    b -= a * mean_img
    del mean_img

    # (box(a) / CNT) * img + (box(b) / CNT)
    q = box(a, radius, np.float64)
    del a
    q /= CNT
    q *= img
    mean_b = box(b, radius, np.float64)
    del b
    mean_b /= CNT
    q += mean_b

    return q
