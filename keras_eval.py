import tensorflow as tf
import tensorflow.python.keras as k
from pathlib import Path
from tools.base import INFO, ERROR, NOTE
import argparse
import numpy as np
from register import dict2obj, network_register, helper_register, eval_register
from yaml import safe_load

tf.enable_v2_behavior()
config = tf.ConfigProto()
config.gpu_options.allow_growth = True
sess = tf.InteractiveSession(config=config)
k.backend.set_session(sess)
k.backend.set_learning_phase(0)


def main(ckpt_path: Path, argmap: dict2obj):

    model, evaluate = argmap.model, argmap.evaluate
    h = helper_register[model.helper](**model.helper_kwarg)

    network = network_register[model.network]
    infer_model, train_model = network(**model.network_kwarg)

    print(INFO, f'Load CKPT from {str(ckpt_path)}')
    infer_model.load_weights(str(ckpt_path))

    eval_fn = eval_register[evaluate.eval_fn]
    eval_fn(infer_model, h, **evaluate.eval_fn_kwarg)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, help='config file path, default in same folder with `pre_ckpt`', default=None)
    parser.add_argument('pre_ckpt', type=str, help='pre-train weights path')
    args = parser.parse_args()

    pre_ckpt = Path(args.pre_ckpt)

    if args.config == None:
        config_path = list(pre_ckpt.parent.glob('*.yml'))[0]  # type: Path
    else:
        config_path = Path(args.config)

    with config_path.open('r') as f:
        cfg = safe_load(f)

    ArgMap = dict2obj(cfg)
    main(Path(args.pre_ckpt), ArgMap)