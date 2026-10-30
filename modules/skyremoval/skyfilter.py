
import time
import numpy as np
import cv2
import os
import onnxruntime as ort
from .guidedfilter import guided_filter
from modules import log
from modules.ai import read_image
from threading import Lock

mutex = Lock()

# uint8 pixel value -> the same value divided by 255, in float32
TO_FLOAT32 = np.array(np.arange(256) / 255., dtype=np.float32)

# Peak memory of run_img per image pixel. tracemalloc measures 40 to 41 bytes
# on 2, 9 and 20 megapixel images; the rest is a margin for buffers it does not
# see (JPEG decoding, OpenCV). Measure again when run_img changes.
BYTES_PER_PIXEL = 48

# Use GPU if it is available, otherwise CPU
provider = "CUDAExecutionProvider" if "CUDAExecutionProvider" in ort.get_available_providers() else "CPUExecutionProvider"

class SkyFilter():

    def __init__(self, model, width = 384, height = 384):

        self.model = model
        self.width, self.height = width, height

        self.load_model()

    
    def load_model(self):
        log.INFO(' -> Loading the model')
        self.session = ort.InferenceSession(self.model, providers=[provider])
        # ONNX Runtime falls back to the CPU when it cannot start the CUDA
        # provider, so log the provider the session chose, not the one asked for
        log.INFO('Sky removal: ONNX Runtime provider %s' % self.session.get_providers()[0])


    @staticmethod
    def memory_per_image(width, height):
        """Peak bytes that run_img needs for one width x height image."""
        return BYTES_PER_PIXEL * width * height


    def get_mask(self, img):

        height, width, c = img.shape

        # Resize image to fit the model input
        new_img = cv2.resize(img, (self.width, self.height), interpolation=cv2.INTER_AREA)
        new_img = np.array(new_img, dtype=np.float32)

        # The guided filter only needs the third channel. Keep a copy of it
        # and drop the full image before the full-size arrays are created.
        guidance = np.ascontiguousarray(img[:,:,2])
        del img

        # Input vector for onnx model
        input_v = np.expand_dims(new_img.transpose((2, 0, 1)), axis=0)
        ort_inputs = {self.session.get_inputs()[0].name: input_v}

        # Run the model
        with mutex:
            ort_outs = self.session.run(None, ort_inputs)

        # Get the output
        output = np.array(ort_outs)
        output = output[0][0].transpose((1, 2, 0))
        output = cv2.resize(output, (width, height), interpolation=cv2.INTER_LANCZOS4)
        np.clip(output, a_max=1.0, a_min=0.0, out=output)

        return self.refine(output, guidance)


    def refine(self, pred, guidance):
        guided_filter_radius, guided_filter_eps = 20, 0.01
        res = guided_filter(guidance, pred, guided_filter_radius, guided_filter_eps)

        np.clip(res, a_min=0, a_max=1, out=res)

        # Convert res to CV_8UC1
        res *= 255.
        res = res.astype(np.uint8)

        # Thresholding
        res = cv2.threshold(res, 127, 255, cv2.THRESH_BINARY_INV)[1]

        return res


    def read_float_image(self, img_path):
        img = read_image(img_path)
        if img.dtype == np.uint8:
            # Same values as img / 255. in float32, without a float64 copy
            return TO_FLOAT32[img]
        return np.array(img / 255., dtype=np.float32)


    def run_img(self, img_path, dest):

        # No local reference to the image here: get_mask frees it as soon as
        # it has taken what it needs
        mask = self.get_mask(self.read_float_image(img_path))

        img_name = os.path.basename(img_path)
        fpath = os.path.join(dest, img_name)

        fname, _ = os.path.splitext(fpath)
        mask_name = fname + '_mask.png'
        cv2.imwrite(mask_name, mask)

        return mask_name
