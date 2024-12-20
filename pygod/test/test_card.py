# -*- coding: utf-8 -*-
import os
import unittest
from numpy.testing import assert_equal
from numpy.testing import assert_raises

import torch
from torch_geometric.nn import GCN
from torch_geometric.seed import seed_everything

from pygod.metric import eval_roc_auc
from pygod.detector import CARD

seed_everything(717)


class TestCARD(unittest.TestCase):
    def setUp(self):
        self.roc_floor = 0.55

        self.train_data = torch.load(os.path.join('pygod/test/train_graph.pt'))
        self.test_data = torch.load(os.path.join('pygod/test/test_graph.pt'))

    def test_full(self):
        detector = CARD(epoch=10, num_layers=2, hid_dim=8)
        detector.fit(self.train_data)

        score = detector.predict(return_pred=False, return_score=True)

        assert (eval_roc_auc(self.train_data.y, score) >= self.roc_floor)

        pred, score, conf = detector.predict(self.test_data,
                                             return_pred=True,
                                             return_score=True,
                                             return_conf=True)

        assert_equal(pred.shape[0], self.test_data.y.shape[0])
        assert (eval_roc_auc(self.test_data.y, score) >= self.roc_floor)
        assert_equal(conf.shape[0], self.test_data.y.shape[0])
        assert (conf.min() >= 0)
        assert (conf.max() <= 1)

        prob = detector.predict(self.test_data,
                                return_pred=False,
                                return_prob=True,
                                prob_method='linear')
        assert_equal(prob.shape[0], self.test_data.y.shape[0])
        assert (prob.min() >= 0)
        assert (prob.max() <= 1)

        prob = detector.predict(self.test_data,
                                return_pred=False,
                                return_prob=True,
                                prob_method='unify')
        assert_equal(prob.shape[0], self.test_data.y.shape[0])
        assert (prob.min() >= 0)
        assert (prob.max() <= 1)

        with assert_raises(ValueError):
            detector.predict(self.test_data,
                             return_prob=True,
                             prob_method='something')

    def test_sample(self):
        detector = CARD(hid_dim=8,
                        num_layers=2,
                        dropout=0.5,
                        weight_decay=0.001,
                        act=torch.nn.functional.relu,
                        backbone=GCN,
                        contamination=0.2,
                        lr=0.001,
                        epoch=10,
                        batch_size=16,
                        num_neigh=4,
                        verbose=3,
                        save_emb=True,
                        act_first=True)
        detector.fit(self.train_data)

        score = detector.predict(return_pred=False, return_score=True)
        assert (eval_roc_auc(self.train_data.y, score) >= self.roc_floor)

        pred, score, conf, emb = detector.predict(self.test_data,
                                                  return_pred=True,
                                                  return_score=True,
                                                  return_conf=True,
                                                  return_emb=True)

        assert_equal(pred.shape[0], self.test_data.y.shape[0])
        assert (eval_roc_auc(self.test_data.y, score) >= self.roc_floor)
        assert_equal(conf.shape[0], self.test_data.y.shape[0])
        assert (conf.min() >= 0)
        assert (conf.max() <= 1)
        assert_equal(emb.shape[0], self.test_data.y.shape[0])
        assert_equal(emb.shape[1], detector.hid_dim)

        prob = detector.predict(self.test_data,
                                return_pred=False,
                                return_prob=True,
                                prob_method='linear')
        assert_equal(prob.shape[0], self.test_data.y.shape[0])
        assert (prob.min() >= 0)
        assert (prob.max() <= 1)

        prob = detector.predict(self.test_data,
                                return_pred=False,
                                return_prob=True,
                                prob_method='unify')
        assert_equal(prob.shape[0], self.test_data.y.shape[0])
        assert (prob.min() >= 0)
        assert (prob.max() <= 1)

        with assert_raises(ValueError):
            detector.predict(self.test_data,
                             return_prob=True,
                             prob_method='something')
