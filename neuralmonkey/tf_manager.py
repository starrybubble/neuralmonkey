"""Interface between the data and TF session.

This module impelements the TensorFlow manager which encapsulates the graph
execution in existing sessions.

"""

# pylint: disable=unused-import
from typing import Any, List, Union

import numpy as np
import tensorflow as tf
from neuralmonkey.logging import log
from neuralmonkey.dataset import Dataset

from neuralmonkey.runners.base_runner import (Executable, ExecutionResult,
                                              reduce_execution_results)

# tests: pylint,mypy


class TensorFlowManager(object):
    """Inteface between computational graph, data and TF sessions.

    Attributes:
        sessions: List of active Tensorflow sessions.
    """

    def __init__(self, num_sessions, num_threads, variable_files=None,
                 gpu_allow_growth=True):
        """Initialize a TensorflowManager.

        At this moment the graph must already exist. This method initializes
        required number of TensorFlow sessions and initializes them with
        provided variable files if they are provided.

        Args:
            num_sessions: Number of sessions to be initialized.
            num_threads: Number of threads sessions will run in.
            variable_files: List of variable files.
        """

        session_cfg = tf.ConfigProto()
        session_cfg.inter_op_parallelism_threads = num_threads
        session_cfg.intra_op_parallelism_threads = num_threads
        session_cfg.allow_soft_placement = True # needed for multiple GPUs
        # pylint: disable=no-member
        session_cfg.gpu_options.allow_growth = gpu_allow_growth

        self.sessions = [tf.Session(config=session_cfg)
                         for _ in range(num_sessions)]
        init_op = tf.initialize_all_variables()
        for sess in self.sessions:
            sess.run(init_op)
        self.saver = tf.train.Saver()

        if variable_files:
            if len(variable_files) != num_sessions:
                raise Exception(("The number of provided variable files ({}) "
                                 "is different than a number sessions ({})")
                                .format(len(variable_files), num_sessions))
            self.restore(variable_files)

    # pylint: disable=too-many-locals
    def execute(self,
                dataset: Dataset,
                execution_scripts,
                train=False,
                summaries=True,
                batch_size=None) -> List[ExecutionResult]:
        if batch_size is None:
            batch_size = len(dataset)
        batched_dataset = dataset.batch_dataset(batch_size)

        batch_results = [[] for _ in
                         execution_scripts]  # type: List[List[ExecutionResult]]
        for batch in batched_dataset:
            executables = [s.get_executable(train=train, summaries=summaries)
                           for s in execution_scripts]
            while not all(ex.result is not None for ex in executables):
                all_feedables = set()   # type: Set[Any]
                all_tensors_to_execute = {}  # type: Dict[Executable, tf.Tensor]
                additional_feed_dicts = []
                tensor_list_lengths = []  # type: List[int]

                for executable in executables:
                    if executable.result is None:
                        (feedables,
                         tensors_to_execute,
                         add_feed_dict) = executable.next_to_execute()
                        all_feedables = all_feedables.union(feedables)
                        all_tensors_to_execute[executable] = tensors_to_execute
                        additional_feed_dicts.append(add_feed_dict)
                        tensor_list_lengths.append(len(tensors_to_execute))
                    else:
                        tensor_list_lengths.append(0)

                feed_dict = _feed_dicts(batch, all_feedables, train=train)
                for fdict in additional_feed_dicts:
                    feed_dict.update(fdict)

                session_results = [sess.run(all_tensors_to_execute,
                                            feed_dict=feed_dict)
                                   for sess in self.sessions]

                for executable in executables:
                    if executable.result is None:
                        executable.collect_results(
                            [res[executable] for res in session_results])

            for script_list, executable in zip(batch_results, executables):
                script_list.append(executable.result)

        collected_results = []  # type: List[ExecutionResult]
        for result_list in batch_results:
            collected_results.append(reduce_execution_results(result_list))

        return collected_results

    def save(self, variable_files: Union[str, List[str]]) -> None:
        if isinstance(variable_files, str) and len(self.sessions) == 1:
            self.saver.save(self.sessions[0], variable_files)
            return

        if isinstance(variable_files, str):
            variable_files = ["{}.{}".format(
                variable_files, i) for i in range(len(self.sessions))]

        if len(variable_files) != len(self.sessions):
            raise Exception(
                "Provided {} files for restoring {} sessions.".format(
                    len(variable_files), len(self.sessions)))

        for sess, file_name in zip(self.sessions, variable_files):
            self.saver.save(sess, file_name)

    def restore(self, variable_files: Union[str, List[str]]) -> None:
        if isinstance(variable_files, str):
            variable_files = [variable_files]
        if len(variable_files) != len(self.sessions):
            raise Exception(
                "Provided {} files for restoring {} sessions.".format(
                    len(variable_files), len(self.sessions)))

        for sess, file_name in zip(self.sessions, variable_files):
            log("Loading variables from {}".format(file_name))
            self.saver.restore(sess, file_name)


def _feed_dicts(dataset, coders, train=False):
    """
    This function ensures all encoder and decoder objects feed their the data
    they need from the dataset.
    """
    res = {}

    for coder in coders:
        res.update(coder.feed_dict(dataset, train=train))

    return res
