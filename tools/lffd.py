import numpy as np
import tensorflow as tf
from skimage.transform import resize, rescale
import imgaug.augmenters as iaa
from imgaug import BoundingBoxesOnImage
from tools.base import BaseHelper, INFO
from typing import List
import matplotlib.pyplot as plt


class LFFDHelper(BaseHelper):
    def __init__(self, image_ann: str, featuremap_size: np.ndarray, in_hw: np.ndarray,
                 neg_resize_factor: np.ndarray, validation_split: float, neg_sample_ratio: float):
        self.train_dataset: tf.data.Dataset = None
        self.val_dataset: tf.data.Dataset = None
        self.test_dataset: tf.data.Dataset = None

        self.train_pos_list: np.ndarray = None
        self.val_pos_list: np.ndarray = None
        self.test_pos_lis: np.ndarray = None

        self.train_neg_list: np.ndarray = None
        self.val_neg_list: np.ndarray = None
        self.test_neg_lis: np.ndarray = None

        self.train_epoch_step: int = None
        self.val_epoch_step: int = None
        self.test_epoch_step: int = None

        self.data: dict = None
        # load dataset
        self.data = np.load(open(image_ann, 'rb'), allow_pickle=True)  # type:dict
        _positive_index = []
        _negative_index = []
        for k, v in self.data.items():
            if v[1] == 0:  # negative
                _negative_index.append(k)
            else:  # positive
                _positive_index.append(k)
        _positive_index = np.array(_positive_index)
        _negative_index = np.array(_negative_index)

        # split dataset
        self.train_pos_list, self.val_pos_list, self.test_pos_list = np.split(
            _positive_index,
            [int((1 - validation_split) * len(_positive_index)),
             int((1 - validation_split / 2) * len(_positive_index))])
        self.train_neg_list, self.val_neg_list, self.test_neg_list = np.split(
            _negative_index,
            [int((1 - validation_split) * len(_negative_index)),
             int((1 - validation_split / 2) * len(_negative_index))])

        self.iaaseq = iaa.OneOf([
            iaa.Fliplr(0.5),  # 50% 镜像
            iaa.Flipud(0.5),
            iaa.Affine(rotate=(-10, 10)),  # 随机旋转
            iaa.Affine(translate_percent={"x": (-0.1, 0.1), "y": (-0.1, 0.1)})  # 随机平移
        ])  # type: iaa.meta.Augmenter

        # set paramter
        self.featuremap_size: np.ndarray = featuremap_size
        self.scale_num: int = self.featuremap_size.size
        self.neg_resize_factor: np.ndarray = neg_resize_factor
        self.in_hw: np.ndarray = in_hw
        self.small_list: np.ndarray = np.flip(featuremap_size) + 1
        self.large_list: np.ndarray = self.small_list * 2
        self.small_weak_list: np.ndarray = np.floor(self.small_list * 0.9).astype(np.int)
        self.large_weak_list: np.ndarray = np.ceil(self.large_list * 1.1).astype(np.int)
        self.stride_list: np.ndarray = in_hw[0] // (featuremap_size + 1)
        self.center_list: np.ndarray = self.stride_list - 1
        self.out_channels = 6
        self.normal_para = self.large_list // 2  # Normalization parameters
        self.neg_sample_ratio: float = neg_sample_ratio  # neg sample ratio

        self.train_total_data = len(self.train_pos_list)
        self.val_total_data = len(self.val_pos_list)
        self.test_total_data = len(self.test_pos_list)

    def read_img(self, stream: np.ndarray) -> tf.Tensor:
        """ read img from raw stream

        Parameters
        ----------
        stream : np.ndarray

        Returns
        -------

        tf.Tensor

            image source dtype = tf.uint8

        """
        return tf.image.decode_image(stream, channels=3,
                                     dtype=tf.uint8, expand_animations=False)

    def _resize_neg_img(self, im_in: np.ndarray, img: np.ndarray):
        """ resize negative image

        Parameters
        ----------
        im_in : np.ndarray

        img : np.ndarray

        """
        im_h, im_w = img.shape[0], img.shape[1]
        in_h, in_w = self.in_hw[0], self.in_hw[1]
        # random resize neg image
        resize_factor = np.random.uniform(self.neg_resize_factor[0],
                                          self.neg_resize_factor[1])
        img = resize(img, [int(im_h * resize_factor),
                           int(im_w * resize_factor)],
                     preserve_range=True).astype(np.uint8)
        # put neg image into the placeholder
        h_gap = im_h - in_h
        w_gap = im_w - in_w
        if h_gap > 0:
            y_top = np.random.randint(0, h_gap)
        else:
            y_pad = int(-h_gap / 2)
        if w_gap > 0:
            x_left = np.random.randint(0, w_gap)
        else:
            x_pad = int(-w_gap / 2)

        if h_gap >= 0 and w_gap >= 0:
            im_in = img[y_top:y_top + in_h, x_left:x_left + in_w, :]
        elif h_gap >= 0 and w_gap < 0:
            im_in[:, x_pad:x_pad + im_w, :] = img[y_top:y_top + in_h]
        elif h_gap < 0 and w_gap >= 0:
            im_in[y_pad:y_pad + im_h] = img[:, x_left:x_left + in_w, :]
        else:
            im_in[y_pad:y_pad + im_h, x_pad:x_pad + im_w, :] = img

    def _resize_pos_img(self, im_in: np.ndarray, img: np.ndarray,
                        boxes: np.ndarray) -> [np.ndarray, np.ndarray,
                                               np.ndarray, np.ndarray]:
        """ resize positive image

        Parameters
        ----------
        im_in : np.ndarray

        img : np.ndarray

        boxes : np.ndarray

        Returns
        -------

        [np.ndarray, np.ndarray, np.ndarray, np.ndarray]

            [boxes, strong_fit, weak_fit, valid]

        """
        target_idx = np.random.randint(len(boxes))
        # select bbox scale
        longer_side = max(boxes[target_idx, 2:])
        if longer_side <= self.small_list[0]:
            scale_idx = 0
        elif longer_side <= self.small_list[1]:
            scale_idx = np.random.randint(2)
        else:
            if np.random.random() > 0.9:
                scale_idx = np.random.randint(self.scale_num + 1)
            else:
                scale_idx = np.random.randint(self.scale_num)

        # random select scale
        if scale_idx == self.scale_num:
            scale_idx -= 1
            side_length = np.random.randint(
                self.large_list[scale_idx],
                self.small_list[scale_idx] + self.large_list[scale_idx])
        else:
            side_length = np.random.randint(
                self.small_list[scale_idx], self.large_list[scale_idx])

        target_scale = side_length / longer_side
        # calculate scale
        boxes = boxes * target_scale

        # init state array
        strong_fit = np.zeros((self.scale_num, len(boxes)), dtype=np.bool)
        weak_fit = np.zeros((self.scale_num, len(boxes)), dtype=np.bool)
        valid = np.zeros((self.scale_num, len(boxes)), dtype=np.bool)

        for i, box in enumerate(boxes):
            longer_side = max(box[2:])
            for j in range(self.scale_num):
                if self.small_list[j] <= longer_side <= self.large_list[j]:
                    strong_fit[j, i] = True
                    valid[j, i] = True
                elif self.small_weak_list[j] <= longer_side <= self.large_weak_list[j]:
                    weak_fit[j, i] = True
                    valid[j, i] = True
        # rescale
        img = rescale(img, target_scale, preserve_range=True,
                      multichannel=True).astype(np.uint8)
        # crop and place the input image centered on the selected box
        vibr = self.stride_list[scale_idx] // 2  # add vibrate
        offset_x = np.random.randint(-vibr, vibr)
        offset_y = np.random.randint(-vibr, vibr)

        center_x = boxes[target_idx, 0] + boxes[target_idx, 2] / 2 + offset_x
        center_y = boxes[target_idx, 1] + boxes[target_idx, 3] / 2 + offset_y
        left = int(center_x - self.in_hw[1] / 2)
        top = int(center_y - self.in_hw[0] / 2)
        right = int(center_x + self.in_hw[1] / 2)
        bottom = int(center_y + self.in_hw[0] / 2)

        if left < 0:
            left_pad = -left
            left = 0
        else:
            left_pad = 0

        if top < 0:
            top_pad = -top
            top = 0
        else:
            top_pad = 0

        img = img[top:bottom, left:right]
        im_in[top_pad:top_pad + img.shape[0],
              left_pad:left_pad + img.shape[1]] = img
        # adjust boxes
        boxes[:, 0] = boxes[:, 0] + left_pad - left
        boxes[:, 1] = boxes[:, 1] + top_pad - top
        return boxes, strong_fit, weak_fit, valid

    def resize_img(self, img: np.ndarray, boxes: np.ndarray = None) -> [np.ndarray, list]:
        """ resize image

        Parameters
        ----------
        img : np.ndarray

        boxes : np.ndarray, optional

            when annoation is None, mean this sampe is negative, by default None

            annoation = [num_box * [left_x, top_y, width, height]]

        Returns
        -------

        [np.ndarray, list]

            image, state_list
            when boxes is not **None**, state_list contain :
                [boxes, strong_fit, weak_fit, valid]

        """
        im_in = np.zeros([self.in_hw[0], self.in_hw[1], 3], dtype=np.uint8)

        if boxes is None:
            self._resize_neg_img(im_in, img)
        else:
            boxes = self._resize_pos_img(im_in, img, boxes)

        return im_in, boxes

    def data_augmenter(self, img: np.ndarray,
                       boxes: np.ndarray = None) -> [np.ndarray, np.ndarray]:
        """ data augmenter

        Parameters
        ----------
        img : np.ndarray

        boxes : np.ndarray, optional

            by default None

        Returns
        -------

        [np.ndarray, np.ndarray]

            img_aug , ann_aug
        """
        if boxes is None:
            image_aug = self.iaaseq(image=img)
            return image_aug, None
        else:
            # todo add augment
            return img, boxes

    def _neg_ann_to_label(self, labels: list, prob_axis: int, bbox_axis: int):
        """ make negative annotation to label

        Parameters
        ----------
        labels : list

        prob_axis : int

        bbox_axis : int

        """
        for label in labels:
            # all negative porb = 1
            label[..., 1] = 1
            # all location porb is valid, all bbox regression is invalid
            label[..., prob_axis] = 1

    def _pos_ann_to_label(self, labels: list, prob_axis: int, bbox_axis: int,
                          boxes: np.ndarray, strong_fit: np.ndarray,
                          weak_fit: np.ndarray, valid: np.ndarray):
        """ make positive annotation to label

        Parameters
        ----------
        labels : list

        prob_axis : int

        bbox_axis : int

        boxes : np.ndarray

            boxes array, [box_num * [x0,y0,x1,y1]]

        strong_fit : np.ndarray

            strong fit area array

        weak_fit : np.ndarray

            weak fit area array

        valid : np.ndarray

            valid area array

        """
        # compute the center coordinates of all receptive fields
        for i in range(self.scale_num):
            # init state
            rf_centers = np.array([
                self.center_list[i] + w * self.stride_list[i]
                for w in range(self.featuremap_size[i])])

            labels[i][..., 1] = 1  # all is negative
            labels[i][..., prob_axis] = 1  # all location porb is valid
            count_strong_fit = np.zeros((self.featuremap_size[i],
                                         self.featuremap_size[i]), dtype=np.int32)
            count_weak_fit = np.zeros((self.featuremap_size[i],
                                       self.featuremap_size[i]), dtype=np.int32)

            for j, (tmp_x0, tmp_y0, tmp_w, tmp_h) in enumerate(boxes):
                if valid[i][j] is False:
                    continue
                tmp_x1 = tmp_x0 + tmp_w
                tmp_y1 = tmp_y0 + tmp_h
                # skip if this bbox is not in the image
                if tmp_x1 <= 0 or tmp_x0 >= self.in_hw[1] \
                        or tmp_y1 <= 0 or tmp_y1 >= self.in_hw[0]:
                    continue

                # calculation of bbox's receptive field coordinates
                rf_x0 = max(0, int((tmp_x0 - self.center_list[i]) /
                                   self.stride_list[i]) + 1)
                rf_x1 = min(self.featuremap_size[i] - 1,
                            int((tmp_x1 - self.center_list[i]) / self.stride_list[i]))
                rf_y0 = max(0, int((tmp_y0 - self.center_list[i]) /
                                   self.stride_list[i]) + 1)
                rf_y1 = min(self.featuremap_size[i] - 1,
                            int((tmp_y1 - self.center_list[i]) / self.stride_list[i]))

                # skip if this receptive field coordinates is wrong
                if rf_x1 < rf_x0 or rf_y1 < rf_y0:
                    continue

                if weak_fit[i][j]:
                    count_weak_fit[rf_y0:rf_y1 + 1, rf_x0:rf_x1 + 1] = 1
                else:
                    count_strong_fit[rf_y0:rf_y1 + 1, rf_x0:rf_x1 + 1] += 1

                    x_centers = rf_centers[rf_x0:rf_x1 + 1]
                    y_centers = rf_centers[rf_y0:rf_y1 + 1]
                    x0 = (x_centers - tmp_x0) / self.normal_para[i]
                    y0 = (y_centers - tmp_y0) / self.normal_para[i]
                    x1 = (x_centers - tmp_x1) / self.normal_para[i]
                    y1 = (y_centers - tmp_y1) / self.normal_para[i]

                    labels[i][rf_y0:rf_y1 + 1, rf_x0:rf_x1 + 1, 2] = x0
                    labels[i][rf_y0:rf_y1 + 1, rf_x0:rf_x1 + 1, 3] = y0[:, None]
                    labels[i][rf_y0:rf_y1 + 1, rf_x0:rf_x1 + 1, 4] = x1
                    labels[i][rf_y0:rf_y1 + 1, rf_x0:rf_x1 + 1, 5] = y1[:, None]

                # filter some overlap points
                # and some points that are only weakly coincident
                weak_fit_flag = np.logical_or(count_strong_fit > 1, count_weak_fit > 0)
                strong_fit_flag = count_strong_fit == 1
                # strong_fit location is positive
                labels[i][..., 0][strong_fit_flag] = 1  # pos prob is 1
                labels[i][..., 1][strong_fit_flag] = 0  # neg prob is 0
                # filter weak_fit location
                labels[i][..., prob_axis][weak_fit_flag] = 0
                # for bbox regression, only strong_fit area is available
                labels[i][..., bbox_axis][strong_fit_flag] = 1

    def ann_to_label(self, boxes: np.ndarray = None) -> (list, list):
        """ convert annotation to label

        Parameters
        ----------
        boxes : np.ndarray, optional

            when boxes is **None** , mean this sample is negative, by default None
            when boxes is **Not None** , mean this sample is postive ,
            And contains :
                [boxes, strong_fit, weak_fit, valid]

        Returns
        -------

        tuple

            m = scale num
            labels = ([label_1, label_2, ..., label_m, mask_score, mask_bbox]
            label shape = [featuremap_size, featuremap_size, output_channels + 2]
            NOTE when debug need split labels -> [labels, masks]
        """
        labels = [np.zeros((v, v, self.out_channels + 2), dtype=np.float32)
                  for v in self.featuremap_size]
        prob_axis = self.out_channels
        bbox_axis = self.out_channels + 1

        if boxes is None:
            self._neg_ann_to_label(labels, prob_axis, bbox_axis)
        else:
            # boxes, strong_fit, weak_fit, valid = boxes
            self._pos_ann_to_label(labels, prob_axis, bbox_axis, *boxes)
        return labels

    def build_datapipe(self, pos_list: np.ndarray, neg_list: np.ndarray,
                       batch_size: int, rand_seed: int, is_augment: bool,
                       is_normlize: bool, is_training: bool) -> tf.data.Dataset:

        print(INFO, 'data augment is ', str(is_augment))

        def _wapper(img, ann, is_augment: bool, is_resize: bool,
                    is_normlize: bool) -> [np.ndarray, tuple]:
            """ wapper for process image and ann to label """
            im, anns = self.process_img(img, ann, is_augment, is_resize, is_normlize)
            labels = self.ann_to_label(anns)
            return (im, *labels)

        def _parser(pos_idx: tf.Tensor, neg_idx: tf.Tensor):
            # NOTE use wrapper function and dynamic list construct
            # (img,(label_1,label_2,...))
            idx = tf.cond(tf.random.uniform(()) < self.neg_sample_ratio,
                          lambda: neg_idx, lambda: pos_idx)
            im_src, ann = tf.numpy_function(lambda i: (self.data[i][0].tostring(),
                                                       self.data[i][2]),
                                            [idx], [tf.string, tf.float32])
            # load image
            raw_img = self.read_img(im_src)
            # resize image -> image augmenter -> make labels
            raw_img, *labels = tf.numpy_function(
                _wapper, [raw_img, ann, is_augment, True, False],
                [tf.uint8] + [tf.float32] * self.scale_num)
            # normlize image
            if is_normlize:
                img = self.normlize_img(raw_img)
            else:
                img = tf.cast(raw_img, tf.float32)

            for i, v in enumerate(self.featuremap_size):
                labels[i].set_shape((v, v, self.out_channels + 2))
            img.set_shape((self.in_hw[0], self.in_hw[1], 3))

            return img, tuple(labels)

        if is_training:
            pos_ds = (tf.data.Dataset.range(len(pos_list)).
                      shuffle(batch_size * 500, rand_seed).repeat())
            neg_ds = (tf.data.Dataset.range(len(neg_list)).
                      shuffle(batch_size * 500, rand_seed).repeat())
            ds = (tf.data.Dataset.zip((pos_ds, neg_ds)).
                  map(_parser, -1).
                  batch(batch_size, True).
                  prefetch(-1))
        else:
            pos_ds = tf.data.Dataset.range(len(pos_list))
            neg_ds = tf.data.Dataset.range(len(pos_list))
            ds = (tf.data.Dataset.from_tensor_slices(
                (tf.range(len(pos_list)), tf.range(len(pos_list)))).
                map(_parser, -1).
                batch(batch_size, True).
                prefetch(-1))

        return ds

    def set_dataset(self, batch_size: int, rand_seed: int, is_augment: bool = True,
                    is_normlize: bool = True, is_training: bool = True):
        self.batch_size = batch_size
        if is_training:
            self.train_dataset = self.build_datapipe(
                self.train_pos_list, self.train_neg_list,
                batch_size, rand_seed, is_augment,
                is_normlize, is_training)
            self.val_dataset = self.build_datapipe(
                self.val_pos_list, self.val_neg_list,
                batch_size, rand_seed, False,
                is_normlize, is_training)

            self.train_epoch_step = self.train_total_data // self.batch_size
            self.val_epoch_step = self.val_total_data // self.batch_size
        else:
            self.test_dataset = self.build_datapipe(
                self.val_list, batch_size, rand_seed,
                False, is_normlize, is_training)
            self.test_epoch_step = self.test_total_data // self.batch_size

    def draw_image(self, img: np.ndarray, labels: list, is_show: bool = True):
        """ darw image with label~

        Parameters
        ----------
        img : np.ndarray

        labels : list

            labels list

        is_show : bool, optional

            by default True

        """
        if labels is None:
            plt.imshow(img.astype(np.uint8))
        else:
            fig, axs = plt.subplots(self.scale_num, 3, figsize=(9, 15))
            for i in range(self.scale_num):
                score_mask, bbox_mask = labels[i][..., 6:7], labels[i][..., 7:8]
                img1 = rescale(score_mask, self.in_hw / score_mask.shape[:2],
                               multichannel=True, preserve_range=True) * np.array([150, 0, 0])
                img2 = (img * rescale(bbox_mask, self.in_hw / bbox_mask.shape[:2],
                                      multichannel=True, preserve_range=True))
                imgs = [img, img1, img2]
                for j in range(3):
                    axs[i, j].imshow(imgs[j].astype(np.uint8))
                    axs[i, j].axis('off')

            plt.subplots_adjust(wspace=0.01, hspace=0.02)

        plt.tight_layout(pad=0., w_pad=0., h_pad=0.)
        if is_show:
            plt.show()