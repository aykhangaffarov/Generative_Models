import tensorflow as tf

from graphs.builder import make_models, load_models
from stats.ae_losses import reconstuction_loss

LOSS_NAME= 'bce'

def create_graph(name, variables_params, restore=None):
    variables_names = [variables['name'] for variables in variables_params]  # ['inference',  'generative']
    variables = make_variables(variables_params=variables_params, model_name=name, restore=restore)

    def get_variables():
        return dict(zip(variables_names, variables))
    return get_variables


def create_losses():
    return dict(zip(['x_logits'], [bce]))

#@tf.function
def bce(inputs, x_logit):
    reconstruction_loss = reconstuction_loss(true_x=inputs, pred_x=x_logit)
    Px_xreconst = tf.reduce_mean(-reconstruction_loss)
    return -Px_xreconst

def make_variables(variables_params, model_name, restore=None):
    variables_names = [variables['name'] for variables in variables_params]
    if restore is None:
        variables = make_models(variables_params)
    else:
        variables = load_models(restore, [model_name + '_' + var for var in variables_names])
    return variables

def encode_fn(model, inputs):
    z = model('inference', [inputs])
    return z

def decode_fn(model, latent, inputs_shape, apply_sigmoid=False):
    x_logits = model('generative', [latent])
    if apply_sigmoid:
        probs = tf.sigmoid(x_logits)
        return tf.reshape(tensor=probs, shape=[-1] + [*inputs_shape], name='x_probs')
    return tf.reshape(tensor=x_logits, shape=[-1] + [*inputs_shape], name='x_logits')

@tf.function
def generate_sample(model, inputs_shape, latent_shape, eps=None):
    if eps is None:
        eps = tf.random.normal(shape=latent_shape)
    generated = decode_fn(model=model, latent=eps, inputs_shape=inputs_shape, apply_sigmoid=True)
    return generated

