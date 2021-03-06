import adler
adler.util.gpu.setup_one_gpu()

from adler.tensorflow import reference_unet

import tensorflow as tf
import numpy as np
import odl
import odl.contrib.tensorflow
from mayo_util import FileLoader, DATA_FOLDER

np.random.seed(0)
name = 'mayo_reference_unet'

sess = tf.InteractiveSession()


# Create ODL data structures
size = 512
space = odl.uniform_discr([-128, -128], [128, 128], [size, size],
                          dtype='float32', weighting=1.0)

# Tomography
# Make a fan beam geometry with flat detector
# Angles: uniformly spaced, n = 360, min = 0, max = 2 * pi
angle_partition = odl.uniform_partition(0, 2 * np.pi, 1000)
# Detector: uniformly sampled, n = 558, min = -30, max = 30
detector_partition = odl.uniform_partition(-360, 360, 1000)
geometry = odl.tomo.FanFlatGeometry(angle_partition, detector_partition,
                                    src_radius=500, det_radius=500)


ray_trafo = odl.tomo.RayTransform(space, geometry)
pseudoinverse = odl.tomo.fbp_op(ray_trafo,
                                filter_type='Hann',
                                frequency_scaling=0.45)

# Create tensorflow layer from odl operator
odl_op_layer = odl.contrib.tensorflow.as_tensorflow_layer(ray_trafo,
                                                          'RayTransform')
odl_op_layer_adjoint = odl.contrib.tensorflow.as_tensorflow_layer(ray_trafo.adjoint,
                                                                  'RayTransformAdjoint')

# User selected paramters
n_data = 1
mu_water = 0.02
photons_per_pixel = 10000


file_loader = FileLoader(DATA_FOLDER, exclude='L286')


def generate_data(validation=False):
    """Generate a set of random data."""
    n_iter = 1 if validation else n_data

    x_true_arr = np.empty((n_iter, space.shape[0], space.shape[1], 1), dtype='float32')
    x_0_arr = np.empty((n_iter, space.shape[0], space.shape[1], 1), dtype='float32')

    for i in range(n_iter):
        if validation:
            fi = DATA_FOLDER + 'L286_FD_3_1.CT.0002.0201.2015.12.22.18.22.49.651226.358225786.npy'
        else:
            fi = file_loader.next_file()

        data = np.load(fi)

        phantom = space.element(np.rot90(data, -1))
        phantom /= 1000.0  # convert go g/cm^3

        data = ray_trafo(phantom)
        data = np.exp(-data * mu_water)

        noisy_data = odl.phantom.poisson_noise(data * photons_per_pixel)
        noisy_data = np.maximum(noisy_data, 1) / photons_per_pixel

        log_noisy_data = np.log(noisy_data) * (-1 / mu_water)

        fbp = pseudoinverse(log_noisy_data)

        x_0_arr[i, ..., 0] = fbp
        x_true_arr[i, ..., 0] = phantom

    return x_0_arr, x_true_arr


with tf.name_scope('placeholders'):
    x_0 = tf.placeholder(tf.float32, shape=[None, size, size, 1], name="x_0")
    x_true = tf.placeholder(tf.float32, shape=[None, size, size, 1], name="x_true")
    is_training = tf.placeholder(tf.bool, shape=(), name='is_training')


with tf.name_scope('correction'):
    dx = reference_unet(x_0, 1,
                        ndim=2,
                        features=64,
                        keep_prob=1.0,
                        use_batch_norm=True,
                        activation='relu',
                        init='he',
                        is_training=is_training,
                        name='unet_dx')

    x_result = x_0 + dx


# Initialize all TF variables
sess.run(tf.global_variables_initializer())

# Add op to save and restore
saver = tf.train.Saver()

if 1:
    saver.restore(sess,
                  adler.tensorflow.util.default_checkpoint_path(name))

# Generate validation data
x_arr_validate, x_true_arr_validate = generate_data(validation=True)


x_result_result = sess.run(x_result,
                      feed_dict={x_true: x_true_arr_validate,
                                 x_0: x_arr_validate,
                                 is_training: False})

import matplotlib.pyplot as plt
from skimage.measure import compare_ssim as ssim
from skimage.measure import compare_psnr as psnr

print(ssim(x_result_result[0, ..., 0], x_true_arr_validate[0, ..., 0]))
print(psnr(x_result_result[0, ..., 0], x_true_arr_validate[0, ..., 0], dynamic_range=np.max(x_true_arr_validate) - np.min(x_true_arr_validate)))

path = name
space.element(x_result_result[..., 0]).show(saveto='{}/x'.format(path))
space.element(x_result_result[..., 0]).show(clim=[0.8, 1.2], saveto='{}/x_windowed'.format(path))
plt.close('all')

el = space.element(x_result_result[..., 0])
el.show('', coords=[[-40, 25], [-25, 25]], clim=[0.8, 1.2], saveto='{}/x_midle'.format(path))