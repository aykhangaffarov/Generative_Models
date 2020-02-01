import logging
import os
import time
from collections import defaultdict
from collections.abc import Iterable

import tensorflow as tf
import numpy as np
from IPython import display
from keras_radam import RAdam

from tqdm import tqdm

from evaluation.quantitive_metrics.metrics import create_metrics
from evaluation.supervised_metrics.compute_metrics import compute_supervised_metrics
from evaluation.unsupervised_metrics.compute_metrics import compute_unsupervised_metrics
from graphs.basics.AE_graph import create_graph, create_losses, encode_fn, decode_fn, generate_sample
from graphs.builder import load_models, save_models
from training.callbacks import EarlyStopping
from utils.data_and_files.file_utils import log, inspect_log
from utils.reporting.logging import log_message
from utils.reporting.ploting import plot_and_save_generated

class autoencoder(tf.keras.Model):
    def __init__(
            self,
            name,
            inputs_shape,
            outputs_shape,
            latent_dim,
            variables_params,
            filepath=None,
            model_fn=create_graph,
            **kwargs

    ):
        self.get_variables = model_fn(
            name=name,
            variables_params=variables_params,
            restore=filepath
        )
        self.inputs_shape = inputs_shape
        self.outputs_shape = outputs_shape
        self.latent_dim = latent_dim
        self.encode_fn = encode_fn
        self.decode_fn = decode_fn
        self.generate_sample = generate_sample #TODO : check
        self.save_models = save_models
        self.load_models = load_models

        # connect the graph x' = decode(encode(x))
        _inputs = {k: v.inputs[0] for k, v in self.get_variables().items() if k=='inference'}
        latent = self.encode(_inputs['inference'])
        x_logits = self.decode(latent)
        _outputs = {'x_logits': x_logits}

        tf.keras.Model.__init__(
            self,
            name=name,
            inputs=_inputs,
            outputs=_outputs,
            **kwargs
        )

        ## rename the outputs
        layer = [layer for layer in self.layers if layer._name.endswith('x_logits')][0]
        layer._name = 'x_logits'
        self.output_names = ['x_logits']


    def compile(self,
              optimizer=RAdam,
              loss=create_losses,
              metrics=create_metrics,
              loss_weights=None,
              sample_weight_mode=None,
              weighted_metrics=None,
              target_tensors=None,
              distribute=None,
              **kwargs):

        tf.keras.Model.compile(self, optimizer=optimizer(), loss=loss(), metrics=metrics())
        print(self.summary())

    def encode(self, inputs):
        if inputs.shape == self.inputs_shape:
            inputs = tf.reshape(inputs, (1, ) + self.inputs_shape)
        inputs = tf.cast(inputs, tf.float32)
        return self.encode_fn(model=self.get_varibale, inputs=inputs)

    def decode(self, latent):
        return self.decode_fn(model=self.get_varibale, latent=latent, inputs_shape=self.inputs_shape)

    def get_varibale(self, var_name, param):
        return self.get_variables()[var_name](*param)

    def save(self,
             filepath,
             overwrite=True,
             include_optimizer=True,
             save_format=None,
             signatures=None,
             options=None):
        file_Name = os.path.join(filepath, self.name)
        self.save_models(file_Name, self.get_variables())

    def feedforwad(self, inputs):
        z = self.encode_fn(model=self.get_varibale, inputs=inputs)
        x_logit = self.decode_fn(model=self.get_varibale, latent=z, inputs_shape=self.inputs_shape)
        return {'x_logit': x_logit, 'latent': z}

    # def train_step(self, inputs,  names):
    #     try:
    #         X = inputs[names[0]]
    #     except:
    #         X = inputs[0]
    #
    #     with tf.GradientTape() as tape:
    #         losses_dict = self.loss_functions()
    #         for loss_name, loss_func in losses_dict.items():
    #             losses_dict[loss_name] = loss_func(inputs=X, predictions=self.feedforwad(X))
    #
    #         losses = -sum([*losses_dict.values()])
    #     gradients = tape.gradient(losses, self.get_trainables([*self.get_variables().values()]))
    #     self.optimizer.apply_gradients(zip(gradients, self.get_trainables([*self.get_variables().values()])))
    #     return losses

    # def evaluate_step(self, inputs, names):
    #     try:
    #         X = inputs[names[0]]
    #     except:
    #         X = inputs[0]
    #     losses_dict = self.loss_functions()
    #     for loss_name, loss_func in losses_dict.items():
    #         losses_dict[loss_name] = loss_func(inputs=X, predictions=self.feedforwad(X))
    #     return losses_dict

    # def get_trainables(self, var_list):
    #     vars = []
    #     for var in var_list:
    #         vars += var.trainable_variables
    #     return vars

    # def reduce_sum_dict(self, inputs, outputs):
    #     assert isinstance(outputs, defaultdict), 'inputs should be of type defaultdict'
    #     for inputs_name, inputs_value in inputs.items():
    #         try:
    #             outputs[inputs_name] += inputs_value.numpy()
    #         except:
    #             outputs[inputs_name] = inputs_value.numpy()
    #     outputs['Total'] = sum([*inputs.values()]).numpy()
    #     return outputs

    # def cast_batch(self, batch):
    #     if isinstance(batch, tuple):
    #         batch = dict(zip(range(len(batch)), batch))
    #     return dict(zip([*batch], list(map(lambda v: tf.cast(v[1], dtype=tf.float32) , batch.items()))))

    def fitx(self, train_dataset, test_dataset,
            instance_names=['image'],
            epochs=10,
            learning_rate=1e-3,
            random_latent=None,
            recoding_dir='./recoding',
            gray_plot=True,
            generate_epoch=5,
            save_epoch=5,
            metric_epoch=10,
            gt_epoch=10,
            gt_data=None
            ):
        assert isinstance(train_dataset, Iterable), 'dataset must be iterable'
        assert isinstance(test_dataset, Iterable), 'dataset must be iterable'

        self.dir_setup(recoding_dir)

        # generate random latent
        latent_shape = [50, self.latent_dim]
        if random_latent is None:
            random_latent = tf.random.normal(shape=latent_shape)

        if generate_epoch:
            generated = self.generate_sample(model=self.get_varibale, inputs_shape=self.inputs_shape, latent_shape=latent_shape, eps=random_latent)
            plot_and_save_generated(generated=generated, epoch=0, path=self.image_gen_dir, gray=gray_plot)

        self.optimizer = None#RAdamOptimizer(learning_rate)

        file_Name = os.path.join(self.csv_log_dir, 'TRAIN_' + self.model_name+'.csv')
        start_epoch = inspect_log(file_Name)

        early_stopper = EarlyStopping(name='on-Test dataset ELBO monitor', patience=5, min_delta=1e-6)
        epochs_pbar = tqdm(iterable=range(start_epoch, start_epoch+epochs), position=0, desc='Epochs Progress')
        for epoch in epochs_pbar:
            # training dataset
            tr_start_time = time.time()
            loss_tr = defaultdict()
            loss_tr['Epoch'] = epoch
            log_message('Training ... ', logging.INFO)
            for i, data_train in enumerate(train_dataset):
                data_train = self.cast_batch(data_train)
                total_loss = self.train_step(inputs=data_train, names=instance_names)
                tr_losses = self.evaluate_step(inputs=data_train, names=instance_names)
                loss_tr = self.reduce_sum_dict(tr_losses, loss_tr)
                epochs_pbar.set_description('Epochs Progress, Training Iterations {}'.format(i))
            tr_end_time = time.time()
            loss_tr['Elapsed'] = '{:06f}'.format(tr_end_time - tr_start_time)

            # testing dataset
            val_start_time = time.time()
            loss_val = defaultdict()
            loss_val['Epoch'] = epoch

            log_message('Testing ... ', logging.INFO)
            tbar = tqdm(iterable=range(100), position=0, desc='Testing ...')
            for i, data_test in enumerate(test_dataset):
                data_test = self.cast_batch(data_test)
                val_losses = self.evaluate_step(inputs=data_test, names=instance_names)
                loss_val = self.reduce_sum_dict(val_losses, loss_val)

                montiored_loss = loss_val['Total']
                tbar.update(i%100)
            val_end_time = time.time()
            loss_val['Elapsed'] = '{:06f}'.format(val_end_time - val_start_time)

            # if epoch%metric_epoch == 0:
            #     # testing dataset
            #     met_start_time = time.time()
            #     met_values = defaultdict()
            #     met_values['Epoch'] = epoch
            #
            #     log_message('Evaluating Mertics ... ', logging.INFO)
            #     tbar = tqdm(iterable=range(100), position=0, desc='Evaluating ...')
            #     for i, data_test in enumerate(test_dataset):
            #         data_test = self.cast_batch(data_test)
            #
            #         data = {'X': data_test[instance_names[0]], 'y': self.feedforwad(data_test[instance_names[0]])}
            #         met_computed = create_metrics(data)
            #         met_values = self.reduce_sum_dict(met_computed, met_values)
            #         tbar.update(i % 100)
            #     met_end_time = time.time()
            #     met_values['Elapsed'] = '{:06f}'.format(met_end_time - met_start_time)
            #
            # if (epoch % gt_epoch == 0) and (gt_data is not None):
            #     # testing dataset
            #     gt_start_time = time.time()
            #     gt_values = defaultdict()
            #     gt_values['Epoch'] = epoch
            #
            #     log_message('Evaluating ground truth data ... ', logging.INFO)
            #     #tbar = tqdm(iterable=range(100), position=0, desc='gt Evaluating ...')

            #     def rep_func(x):
            #         return self.feedforwad(x)['latent']
            #
            #     us_scores = compute_unsupervised_metrics(
            #         ground_truth_data=gt_data,
            #         representation_function=rep_func,
            #         random_state=np.random.RandomState(0),
            #         num_train=10000,
            #         batch_size=32
            #     )
            #     s_scores = compute_supervised_metrics(
            #         ground_truth_data=gt_data,
            #         representation_function=rep_func,
            #         random_state=np.random.RandomState(0),
            #         num_train=10000,
            #         num_test=2000,
            #         continuous_factors=False,
            #         batch_size=32
            #     )
            #
            # #############################
            #
            # display.clear_output(wait=False)
            # log_message("==================================================================", logging.INFO)
            # file_Name = os.path.join(self.csv_log_dir, 'TRAIN_' + self.model_name)
            # log(file_name=file_Name, message=dict(loss_tr), printed=True)
            # log_message("==================================================================", logging.INFO)
            #
            # log_message("==================================================================", logging.INFO)
            # file_Name = os.path.join(self.csv_log_dir, 'TEST_' + self.model_name)
            # log(file_name=file_Name, message=dict(loss_val), printed=True)
            # log_message("==================================================================", logging.INFO)
            #
            # if epoch%metric_epoch:
            #     log_message("==================================================================", logging.INFO)
            #     file_Name = os.path.join(self.csv_log_dir, 'Metrics_' + self.model_name)
            #     log(file_name=file_Name, message=dict(met_values), printed=True)
            #     log_message("==================================================================", logging.INFO)
            #
            # if epoch % gt_epoch and gt_data is not None:
            #     gt_metrics = {**s_scores, **us_scores}
            #     log_message("==================================================================", logging.INFO)
            #     file_Name = os.path.join(self.csv_log_dir, 'GroundTMetrics_' + self.model_name)
            #     log(file_name=file_Name, message=dict(gt_metrics), printed=True)
            #     log_message("==================================================================", logging.INFO)
            #
            # if generate_epoch is not None and epoch%generate_epoch==0:
            #     generated = self.generate_sample(model=self.get_varibale, inputs_shape=self.inputs_shape, latent_shape=latent_shape, eps=random_latent)
            # plot_and_save_generated(generated=generated, epoch=epoch, path=self.image_gen_dir,
            #                         gray=gray_plot, save=epoch%generate_epoch==0)

            # if epoch%save_epoch == 0:
            #     log_message('Saving Status in Epoch {}'.format(epoch), logging.CRITICAL)
            #     self.save_status()
            #
            # # Early stopping
            # if (early_stopper.stop(montiored_loss)):
            #     log_message('Aborting Training after {} epoch because no progress ... '.format(epoch), logging.WARN)
            #     break

    # def dir_setup(self, recoding_dir):
    #     self.recoding_dir = recoding_dir
    #     self.csv_log_dir = os.path.join(self.recoding_dir, 'csv_log_dir')
    #     self.image_gen_dir = os.path.join(self.recoding_dir, 'image_gen_dir')
    #     self.var_save_dir = os.path.join(self.recoding_dir, 'var_save_dir')
    #
    #     create_if_not_exist([self.recoding_dir, self.csv_log_dir, self.image_gen_dir, self.var_save_dir])

