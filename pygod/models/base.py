# -*- coding: utf-8 -*-
"""Base class for all outlier detector models
"""
# Author: Yue Zhao <zhaoy@cmu.edu>
# License: BSD 2 clause

import warnings
from collections import defaultdict

from inspect import signature

import six
import numpy as np
from numpy import percentile
from scipy.special import erf
from scipy.stats import binom
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import roc_auc_score
from sklearn.utils.validation import check_is_fitted
from sklearn.utils.multiclass import check_classification_targets

import torch
import argparse
import os.path as osp


class BaseDetector(object):
    """Abstract class for all outlier detection algorithms.

    Parameters
    ----------
    contamination : float in (0., 0.5), optional (default=0.1)
        The amount of contamination of the data set,
        i.e. the proportion of outliers in the data set. Used when fitting to
        define the threshold on the decision function.

    Attributes
    ----------
    decision_scores_ : numpy array of shape (n_samples,)
        The outlier scores of the training data.
        The higher, the more abnormal. Outliers tend to have higher
        scores. This value is available once the detector is fitted.

    threshold_ : float
        The threshold is based on ``contamination``. It is the
        ``n_samples * contamination`` most abnormal samples in
        ``decision_scores_``. The threshold is calculated for generating
        binary outlier labels.

    labels_ : int, either 0 or 1
        The binary labels of the training data. 0 stands for inliers
        and 1 for outliers/anomalies. It is generated by applying
        ``threshold_`` on ``decision_scores_``.
    """

    def __init__(self, contamination=0.1):

        if not (0. < contamination <= 0.5):
            raise ValueError("contamination must be in (0, 0.5], "
                             "got: %f" % contamination)

        self.contamination = contamination

    def fit(self, G):
        """Fit detector. y is ignored in unsupervised methods.

        Parameters
        ----------
        G : PyTorch Geometric Data instance (torch_geometric.data.Data)
            The input graph.

        Returns
        -------
        self : object
            Fitted estimator.
        """
        pass

    def decision_function(self, G):
        """Predict raw anomaly scores of PyG Graph G using the fitted detector.
        The anomaly score of an input sample is computed based on the fitted
        detector. For consistency, outliers are assigned with
        higher anomaly scores.

        Parameters
        ----------
        G : PyTorch Geometric Data instance (torch_geometric.data.Data)
            The input graph.

        Returns
        -------
        anomaly_scores : numpy array of shape (n_samples,)
            The anomaly score of The input graph..
        """
        pass

    def process_graph(self, G):
        """Process the raw PyG data object into a tuple of sub data objects
        needed for the underlying model. For instance, if the training of the
        model need the node feature and edge index, return (G.x, G.edge_index).

        Parameters
        ----------
        G : PyTorch Geometric Data instance (torch_geometric.data.Data)
            The input graph.

        Returns
        -------
        processed_data : tuple of data object
            The necessary information from the raw PyG Data object.
        """
        pass

    def predict(self, G, return_confidence=False):
        """Predict if a particular sample is an outlier or not.

        Parameters
        ----------
        G : PyTorch Geometric Data instance (torch_geometric.data.Data)
            The input graph.

        Returns
        -------
        outlier_labels : numpy array of shape (n_samples,)
            For each observation, tells whether or not
            it should be considered as an outlier according to the
            fitted model. 0 stands for inliers and 1 for outliers.

        confidence : numpy array of shape (n_samples,).
            Only if return_confidence is set to True.
        """

        check_is_fitted(self, ['decision_scores_', 'threshold_', 'labels_'])
        pred_score = self.decision_function(G)
        prediction = (pred_score > self.threshold_).astype('int').ravel()

        if return_confidence:
            confidence = self.predict_confidence(G)
            return prediction, confidence

        return prediction

    def predict_proba(self, G, method='linear', return_confidence=False):
        """Predict the probability of a sample being outlier. Two approaches
        are possible:
        1. simply use Min-max conversion to linearly transform the outlier
           scores into the range of [0,1]. The model must be
           fitted first.
        2. use unifying scores, see :cite:`kriegel2011interpreting`.

        Parameters
        ----------
        G : PyTorch Geometric Data instance (torch_geometric.data.Data)
            The input graph.

        method : str, optional (default='linear')
            probability conversion method. It must be one of
            'linear' or 'unify'.

        return_confidence : boolean, optional(default=False)
            If True, also return the confidence of prediction.

        Returns
        -------
        outlier_probability : numpy array of shape (n_samples, n_classes)
            For each observation, tells whether or not
            it should be considered as an outlier according to the
            fitted model. Return the outlier probability, ranging
            in [0,1]. Note it depends on the number of classes, which is by
            default 2 classes ([proba of normal, proba of outliers]).
        """

        check_is_fitted(self, ['decision_scores_', 'threshold_', 'labels_'])
        train_scores = self.decision_scores_

        test_scores = self.decision_function(G)

        probs = np.zeros([len(test_scores), 2])

        if method == 'linear':
            scaler = MinMaxScaler().fit(train_scores.reshape(-1, 1))
            probs[:, 1] = scaler.transform(
                test_scores.reshape(-1, 1)).ravel().clip(0, 1)
            probs[:, 0] = 1 - probs[:, 1]

            if return_confidence:
                confidence = self.predict_confidence(G)
                return probs, confidence

            return probs

        elif method == 'unify':
            # turn output into probability
            pre_erf_score = (test_scores - self._mu) / (
                    self._sigma * np.sqrt(2))
            erf_score = erf(pre_erf_score)
            probs[:, 1] = erf_score.clip(0, 1).ravel()
            probs[:, 0] = 1 - probs[:, 1]

            if return_confidence:
                confidence = self.predict_confidence(G)
                return probs, confidence

            return probs
        else:
            raise ValueError(method,
                             'is not a valid probability conversion method')

    def predict_confidence(self, G):
        """Predict the model's confidence in making the same prediction
        under slightly different training sets.
        See :cite:`perini2020quantifying`.

        Parameters
        ----------
        G : PyTorch Geometric Data instance (torch_geometric.data.Data)
            The input graph.

        Returns
        -------
        confidence : numpy array of shape (n_samples,)
            For each observation, tells how consistently the model would
            make the same prediction if the training set was perturbed.
            Return a probability, ranging in [0,1].

        """

        check_is_fitted(self, ['decision_scores_', 'threshold_', 'labels_'])

        n = len(self.decision_scores_)

        # todo: this has an optimization opportunity since the scores may
        # already be available
        test_scores = self.decision_function(G)

        count_instances = np.vectorize(
            lambda x: np.count_nonzero(self.decision_scores_ <= x))
        n_instances = count_instances(test_scores)

        # Derive the outlier probability using Bayesian approach
        posterior_prob = np.vectorize(lambda x: (1 + x) / (2 + n))(n_instances)

        # Transform the outlier probability into a confidence value
        confidence = np.vectorize(
            lambda p: 1 - binom.cdf(n - np.int(n * self.contamination), n, p))(
            posterior_prob)
        prediction = (test_scores > self.threshold_).astype('int').ravel()
        np.place(confidence, prediction == 0, 1 - confidence[prediction == 0])

        return confidence

    def _set_n_classes(self, y):
        """Set the number of classes if `y` is presented, which is not
        expected. It could be useful for multi-class outlier detection.

        Parameters
        ----------
        y : numpy array of shape (n_samples,)
            Ground truth.
        Returns
        -------
        self
        """

        self._classes = 2  # default as binary classification
        if y is not None:
            check_classification_targets(y)
            self._classes = len(np.unique(y))
            warnings.warn(
                "y should not be presented in unsupervised learning.")
        return self

    def _process_decision_scores(self):
        """Internal function to calculate key attributes:
        - threshold_: used to decide the binary label
        - labels_: binary labels of training data
        Returns
        -------
        self
        """

        self.threshold_ = percentile(self.decision_scores_,
                                     100 * (1 - self.contamination))
        self.labels_ = (self.decision_scores_ > self.threshold_).astype(
            'int').ravel()

        # calculate for predict_proba()

        self._mu = np.mean(self.decision_scores_)
        self._sigma = np.std(self.decision_scores_)

        return

    # noinspection PyMethodParameters
    def _get_param_names(cls):
        # noinspection PyPep8
        """Get parameter names for the estimator
        See http://scikit-learn.org/stable/modules/generated/sklearn.base.BaseEstimator.html
        and sklearn/base.py for more information.
        """

        # fetch the constructor or the original constructor before
        # deprecation wrapping if any
        init = getattr(cls.__init__, 'deprecated_original', cls.__init__)
        if init is object.__init__:
            # No explicit constructor to introspect
            return []

        # introspect the constructor arguments to find the model parameters
        # to represent
        init_signature = signature(init)
        # Consider the constructor parameters excluding 'self'
        parameters = [p for p in init_signature.parameters.values()
                      if p.name != 'self' and p.kind != p.VAR_KEYWORD]
        for p in parameters:
            if p.kind == p.VAR_POSITIONAL:
                raise RuntimeError("scikit-learn estimators should always "
                                   "specify their parameters in the signature"
                                   " of their __init__ (no varargs)."
                                   " %s with constructor %s doesn't "
                                   " follow this convention."
                                   % (cls, init_signature))
        # Extract and sort argument names excluding 'self'
        return sorted([p.name for p in parameters])

    # noinspection PyPep8
    def get_params(self, deep=True):
        """Get parameters for this estimator.
        See http://scikit-learn.org/stable/modules/generated/sklearn.base.BaseEstimator.html
        and sklearn/base.py for more information.
        Parameters
        ----------
        deep : bool, optional (default=True)
            If True, will return the parameters for this estimator and
            contained subobjects that are estimators.
        Returns
        -------
        params : mapping of string to any
            Parameter names mapped to their values.
        """

        out = dict()
        for key in self._get_param_names():
            # We need deprecation warnings to always be on in order to
            # catch deprecated param values.
            # This is set in utils/__init__.py but it gets overwritten
            # when running under python3 somehow.
            warnings.simplefilter("always", DeprecationWarning)
            try:
                with warnings.catch_warnings(record=True) as w:
                    value = getattr(self, key, None)
                if len(w) and w[0].category == DeprecationWarning:
                    # if the parameter is deprecated, don't show it
                    continue
            finally:
                warnings.filters.pop(0)

            # XXX: should we rather test if instance of estimator?
            if deep and hasattr(value, 'get_params'):
                deep_items = value.get_params().items()
                out.update((key + '__' + k, val) for k, val in deep_items)
            out[key] = value
        return out

    def set_params(self, **params):
        # noinspection PyPep8
        """Set the parameters of this estimator.
        The method works on simple estimators as well as on nested objects
        (such as pipelines). The latter have parameters of the form
        ``<component>__<parameter>`` so that it's possible to update each
        component of a nested object.
        See http://scikit-learn.org/stable/modules/generated/sklearn.base.BaseEstimator.html
        and sklearn/base.py for more information.
        Returns
        -------
        self : object
        """

        if not params:
            # Simple optimization to gain speed (inspect is slow)
            return self
        valid_params = self.get_params(deep=True)

        nested_params = defaultdict(dict)  # grouped by prefix
        for key, value in params.items():
            key, delim, sub_key = key.partition('__')
            if key not in valid_params:
                raise ValueError('Invalid parameter %s for estimator %s. '
                                 'Check the list of available parameters '
                                 'with `estimator.get_params().keys()`.' %
                                 (key, self))

            if delim:
                nested_params[key][sub_key] = value
            else:
                setattr(self, key, value)

        for key, sub_params in nested_params.items():
            valid_params[key].set_params(**sub_params)

        return self

    def __repr__(self):
        # noinspection PyPep8
        """
        See http://scikit-learn.org/stable/modules/generated/sklearn.base.BaseEstimator.html
        and sklearn/base.py for more information.
        """

        class_name = self.__class__.__name__
        return '%s(%s)' % (class_name, _pprint(self.get_params(deep=False),
                                               offset=len(class_name), ),)


def _pprint(params, offset=0, printer=repr):
    # noinspection PyPep8
    """Pretty print the dictionary 'params'
    See http://scikit-learn.org/stable/modules/generated/sklearn.base.BaseEstimator.html
    and sklearn/base.py for more information.
    :param params: The dictionary to pretty print
    :type params: dict
    :param offset: The offset in characters to add at the begin of each line.
    :type offset: int
    :param printer: The function to convert entries to strings, typically
        the builtin str or repr
    :type printer: callable
    :return: None
    """

    # Do a multi-line justified repr:
    options = np.get_printoptions()
    np.set_printoptions(precision=5, threshold=64, edgeitems=2)
    params_list = list()
    this_line_length = offset
    line_sep = ',\n' + (1 + offset // 2) * ' '
    for i, (k, v) in enumerate(sorted(six.iteritems(params))):
        if type(v) is float:
            # use str for representing floating point numbers
            # this way we get consistent representation across
            # architectures and versions.
            this_repr = '%s=%s' % (k, str(v))
        else:
            # use repr of the rest
            this_repr = '%s=%s' % (k, printer(v))
        if len(this_repr) > 500:
            this_repr = this_repr[:300] + '...' + this_repr[-100:]
        if i > 0:
            if this_line_length + len(this_repr) >= 75 or '\n' in this_repr:
                params_list.append(line_sep)
                this_line_length = len(line_sep)
            else:
                params_list.append(', ')
                this_line_length += 2
        params_list.append(this_repr)
        this_line_length += len(this_repr)

    np.set_printoptions(**options)
    lines = ''.join(params_list)
    # Strip trailing space to avoid nightmare in doctests
    lines = '\n'.join(l.rstrip(' ') for l in lines.split('\n'))
    return lines
