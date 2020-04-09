"""
Note: tried to "refactor" the code. Don't do it.
    Tensorflow have problems with imports either is difficult to
    set the typing with tf classes (some problems with found tf.python module, idk)

Takeaway: don't focus too much on code extendibility
"""
import datetime
import os
import sys
import logging
import argparse
import numpy as np
import tensorflow as tf

from os.path import basename, normpath
from typing import Tuple, List

from tensorflow.python.data.ops.dataset_ops import BatchDataset
from tensorflow.python.keras.engine.sequential import Sequential
from pistoBot.utils.general_utils import load_yaml, my_init
from pistoBot.utils.dataset_utils import read_dataset, text_parser, text_tokenizer, create_vocabulary, print_input_batch


def dataset_preprocessor(file_path: str,
                         file_encoding: str,
                         token_level: str,
                         lowercase: bool) -> Tuple[List[str], dict, np.ndarray]:
    text = read_dataset(file_path, file_encoding)
    text = text_parser(text, lowercase=lowercase)
    text_tokenized = text_tokenizer(text, token_level)
    token2idx, idx2token = create_vocabulary(text_tokenized)
    logging.debug(f"Text composed by ({len(text)}|{len(token2idx)}) (tot|unique) tokens")
    return text_tokenized, token2idx, idx2token


def split_input_target(chunk):
    input_text = chunk[:-1]
    target_text = chunk[1:]
    return input_text, target_text


def dataset_prepare(text_tokenized: List[str],
                    token2idx: dict,
                    seq_length: int,
                    batch_size: int,
                    buffer_size: int = 1000) -> BatchDataset:
    buffer_size = buffer_size if buffer_size != 0 else len(text_tokenized)

    text_encoded = np.array([token2idx[token] for token in text_tokenized])
    dataset_ml = tf.data.Dataset.from_tensor_slices(text_encoded)
    dataset_ml = dataset_ml.batch(batch_size=seq_length + 1, drop_remainder=True)  # +1 is the label
    dataset_ml = dataset_ml.map(split_input_target)
    dataset_ml = dataset_ml.shuffle(buffer_size, reshuffle_each_iteration=True)
    dataset_ml = dataset_ml.batch(batch_size, drop_remainder=True)
    return dataset_ml


def print_model_exploration(model, dataset_ml, idx2token):
    logging.debug(model.summary())
    # Network test (from ingestion to prediction)
    for input_x, label_y in dataset_ml.take(1):
        batch_prediction_example = model(input_x)
        logging.info(f"prediction shape: {batch_prediction_example.shape} | [batch, seq_len, vocab_size]")

    # take 1 element according to categorical distribution given by last NN dense layer
    # Note: It is important to sample from this distribution as taking the argmax
    # of the distribution can easily get the model stuck in a loop.
    sampled_indices = tf.random.categorical(batch_prediction_example[0], num_samples=1)
    sampled_indices = tf.squeeze(sampled_indices, axis=-1).numpy()
    logging.debug("Example Input: {}".format(repr(" ".join(idx2token[input_x[0].numpy()]))))
    logging.debug("Example NN output: {}".format(repr(" ".join(idx2token[sampled_indices]))))
    logging.debug("Example NN expected: {}".format(repr(" ".join(idx2token[label_y[0].numpy()]))))


def build_nn(params_ml: dict, vocab_size: int, seq_length: int, batch_size: int) -> Sequential:
    model = tf.keras.Sequential(name="my_vanilla_rnn")

    model.add(tf.keras.layers.Embedding(input_dim=vocab_size,
                                        output_dim=params_ml["embedding_dim"],
                                        batch_input_shape=[batch_size, seq_length]))

    model.add(tf.keras.layers.GRU(units=params_ml["rnn_units"],
                                  return_sequences=True,
                                  stateful=True,
                                  recurrent_initializer='glorot_uniform',
                                  dropout=params_ml["dropout"]))

    model.add(tf.keras.layers.Dense(vocab_size))

    def loss(labels, logits):
        return tf.keras.losses.sparse_categorical_crossentropy(labels, logits, from_logits=True)

    model.compile(optimizer='adam', loss=loss)
    return model


def train_model(model, dataset_ml, params_ml):
    timestamp = datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')
    model_path = os.path.join(params_ml['save_path'], timestamp)
    checkpoint_prefix = os.path.join(model_path, "ckpt_{epoch}")  # The system will fill _epoch_
    checkpoint_callback = tf.keras.callbacks.ModelCheckpoint(filepath=checkpoint_prefix,
                                                             save_weights_only=True)
    history = model.fit(dataset_ml, epochs=params_ml['epochs'], callbacks=[checkpoint_callback])
    return history, model_path


def run(path_params: str):
    # Load params
    params = load_yaml(path_params)
    params_data = params['data']
    params_ml = params['ml']
    logging.info(f"Input params:{params}")

    # Load input
    text_tokenized, token2idx, idx2token = dataset_preprocessor(params_data['file_path'],
                                                                params_data['file_encoding'],
                                                                params_data['token_level'],
                                                                params_data['lowercase'])
    dataset_ml = dataset_prepare(text_tokenized,
                                 token2idx,
                                 params_data['seq_length'],
                                 params_data['batch_size'],
                                 params_data['buffer_size'])

    if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
        print_input_batch(dataset_ml, idx2token)

    # Build network
    model = build_nn(params_ml=params_ml,
                     vocab_size=len(token2idx),
                     seq_length=params_data["seq_length"],
                     batch_size=params_data["batch_size"])

    if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
        print_model_exploration(model, dataset_ml, idx2token)

    # Train model
    train_model(model, dataset_ml, params_ml)

    logging.info("Training completed")


def main(argv):
    parser = argparse.ArgumentParser(prog=argv[0])
    parser.add_argument("--path_params", help="Path to vanilla rnn YAML params", default="./rnn_vanilla_params.yaml")
    parser.add_argument("-v", "--verbose", help="increase output verbosity", action="store_true")
    args = parser.parse_args(argv[1:])
    loglevel = logging.DEBUG if args.verbose else logging.INFO
    process_name = basename(normpath(argv[0]))
    logging.basicConfig(format=f"[{process_name}][%(levelname)s]: %(message)s", level=loglevel, stream=sys.stdout)
    delattr(args, "verbose")
    run_initialized = my_init(run)
    run_initialized(**vars(args))


if __name__ == '__main__':
    main(sys.argv)
