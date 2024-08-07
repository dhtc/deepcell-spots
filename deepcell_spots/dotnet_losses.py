# Copyright 2019-2023 The Van Valen Lab at the California Institute of
# Technology (Caltech), with support from the Paul Allen Family Foundation,
# Google, & National Institutes of Health (NIH) under Grant U24CA224309-01.
# All rights reserved.
#
# Licensed under a modified Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.github.com/vanvalenlab/deepcell-spots/LICENSE
#
# The Work provided may be used for non-commercial academic purposes only.
# For any other use of the Work, including commercial use, please contact:
# vanvalenlab@gmail.com
#
# Neither the name of Caltech nor the names of its contributors may be used
# to endorse or promote products derived from this software without specific
# prior written permission.
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Custom loss functions for DeepCell spots"""

import tensorflow as tf
from deepcell import losses
from tensorflow.keras import backend as K


def smooth_l1(y_true, y_pred, sigma=3.0):
    """Compute the smooth L1 loss of `y_pred` w.r.t. `y_true`.

    Similar to ``deepcell.losses.smooth_l1`` without summation over
    channel axis.

    Args:
        y_true: Tensor from the generator of shape `(B, ?, ?)`.
            The last value for each box is the state of the anchor
            (ignore, negative, positive).
        y_pred: Tensor from the network of shape `(B, ?, ?)`.
            Same shape as `y_true`.
        sigma: The point where the loss changes from L2 to L1.

    Returns:
        The pixelwise smooth L1 loss of `y_pred` w.r.t. `y_true`.
        Has same shape as each of the inputs: `(B, ?, ?)`.
    """
    sigma_squared = sigma ** 2

    # compute smooth L1 loss
    # f(x) = 0.5 * (sigma * x)^2          if |x| < 1 / sigma^2
    #        |x| - 0.5 / sigma^2            otherwise
    regression_diff = K.abs(y_true - y_pred)  # |y - f(x)|

    regression_loss = tf.where(
        K.less(regression_diff, 1.0 / sigma_squared),
        0.5 * sigma_squared * K.pow(regression_diff, 2),
        regression_diff - 0.5 / sigma_squared)
    return regression_loss


class DotNetLosses(object):
    def __init__(self,
                 gamma=2.0,
                 sigma=3.0,
                 n_classes=2,
                 focal=False,
                 d_pixels=1,
                 mu=0,
                 beta=0):
        self.gamma = gamma
        self.sigma = sigma
        self.n_classes = n_classes
        self.focal = focal
        self.d_pixels = d_pixels
        self.mu = mu
        self.beta = beta

    def regression_loss(self, y_true, y_pred):
        """
        Calculates the regression loss of the shift from pixel center, only
        for pixels containing a dot (true regression shifts smaller in
        absolute value than 0.5).

        Args:
            y_true: tensor of shape `(batch, Ly, Lx, 2)`.
            y_pred: tensor of shape `(batch, Ly, Lx, 2)`.
                `Ly`, `Lx` are the dimensions of a single image.
                Dimension 3 contains `delta_y` and `delta_x`.

        Returns:
            float: the normalized smooth  L1 loss over all the input pixels with
            regressed point within the same pixel, i.e. `delta_y = y(...,0)` and
            `delta_x = y(...,1) <= 0.5` in absolute value.
        """
        # get class parameter
        d_pixels = self.d_pixels
        sigma = self.sigma

        # separate x and y offset
        y_offset_true = y_true[..., 0]
        x_offset_true = y_true[..., 1]

        y_offset_pred = y_pred[..., 0]
        x_offset_pred = y_pred[..., 1]

        # calculate the loss only over d_pixels around pixels that contain a point
        # threshold delta_x & delta_y offset of pixel center from point for inclusion in
        # regression loss
        d = 0.5 + d_pixels
        # true if y value is within range d from point containing pixel
        near_pt_y = tf.math.logical_and(
            K.less_equal(-d, y_offset_true), K.less(y_offset_true, d))
        # true if x value is within range d from point containing pixel
        near_pt_x = tf.math.logical_and(
            K.less_equal(-d, x_offset_true), K.less(x_offset_true, d))
        near_pt_indices = tf.where(tf.math.logical_and(near_pt_y, near_pt_x))
        # Classification of half-integer coordinates is inconsistent with the generator. This is
        # negligible for random positions
        # The generator uses python's round which is banker's rounding (to nearest even number)

        y_offset_true_cp = tf.gather_nd(y_offset_true, near_pt_indices)
        x_offset_true_cp = tf.gather_nd(x_offset_true, near_pt_indices)

        y_offset_pred_cp = tf.gather_nd(y_offset_pred, near_pt_indices)
        x_offset_pred_cp = tf.gather_nd(x_offset_pred, near_pt_indices)

        # use smooth l1 loss on the offsets
        pixelwise_loss_y = smooth_l1(
            y_offset_true_cp, y_offset_pred_cp, sigma=sigma)
        pixelwise_loss_x = smooth_l1(
            x_offset_true_cp, x_offset_pred_cp, sigma=sigma)

        # compute the normalizer: the number of positive pixels
        normalizer = K.maximum(1, K.shape(near_pt_indices)[0])
        normalizer = K.cast(normalizer, dtype=K.floatx())

        loss = tf.concat([pixelwise_loss_y, pixelwise_loss_x], axis=-1)

        return K.sum(loss) / normalizer

    def classification_loss(self, y_true, y_pred):
        """
        Args:
            y_true: numpy array of size `(batch, Ly, Lx, 2)`
                one hot encoded pixel classification.
            y_pred: numpy array of size `(batch, Ly, Lx, 2)`
                one hot encoded pixel classification.

        Returns:
            float: focal / weighted categorical cross entropy loss
        """
        focal = self.focal
        gamma = self.gamma
        n_classes = self.n_classes

        if focal:
            loss = losses.weighted_focal_loss(
                y_true, y_pred, gamma=gamma, n_classes=n_classes)

        else:
            loss = losses.weighted_categorical_crossentropy(
                y_true, y_pred, n_classes=n_classes)
            loss = K.mean(loss)

        return loss

    def classification_loss_regularized(self, y_true, y_pred):
        """Regularized classification loss.

        Args:
            y_true: numpy array of size `(batch, Ly, Lx, 2)`
                one hot encoded pixel classification.
            y_pred: numpy array of size `(batch, Ly, Lx, 2)`
                one hot encoded pixel classification.

        Returns:
            float: focal / weighted categorical cross entropy loss
        """
        # get class parameters
        focal = self.focal
        mu = self.mu
        beta = self.beta
        gamma = self.gamma
        n_classes = self.n_classes

        if focal:
            loss = losses.weighted_focal_loss(
                y_true, y_pred, gamma=gamma, n_classes=n_classes)

        else:
            loss = losses.weighted_categorical_crossentropy(
                y_true, y_pred, n_classes=n_classes)
            loss = K.mean(loss)

        # L2 penalize a difference in total number of spots in each image of the batch
        # N_diff = K.sum(y_pred[..., 1], axis=[1, 2]) - K.sum(y_true[..., 1], axis=[1, 2])
        # # shape: batch
        # replace sum by mean to prevent divergence (mean = sum / (Ly*Lx))
        N_diff = K.mean(y_pred[..., 1], axis=[1, 2]) - \
            K.mean(y_true[..., 1], axis=[1, 2])
        N_loss = K.mean(K.square(N_diff))

        # interaction term to reduce tendency to produce false positives near every
        # true point-containing pixel
        # interaction term = mean over all neighbor pairs (i,j) of y_pred_i * y_pred_j
        y_pred_padded = K.spatial_2d_padding(y_pred, padding=((1, 1), (1, 1)))
        inter_loss = K.sum(y_pred_padded[:, 1:, :, 1] * y_pred_padded[:, :-1, :, 1]) + \
            K.sum(y_pred_padded[:, :, 1:, 1] * y_pred_padded[:, :, :-1, 1])

        normalizer = K.cast(
            K.shape(y_pred)[0] * K.shape(y_pred)[1] * K.shape(y_pred)[2], K.floatx())
        inter_loss = inter_loss / normalizer

        return loss + mu * N_loss + beta * inter_loss
