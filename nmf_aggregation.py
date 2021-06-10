#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import sys
import os
import pickle
import random
import re
import torch
import utils
import itertools

import numpy as np
import scipy.io

from collections import defaultdict
from typing import Tuple, List
from sklearn.metrics import r2_score
from sklearn.decomposition import NMF
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import KFold, RepeatedKFold

from plotting import plot_nmf_correlations, plot_r2_scores

def parseargs():
    parser = argparse.ArgumentParser()
    def aa(*args, **kwargs):
        parser.add_argument(*args, **kwargs)
    aa('--in_path', type=str,
        help='path to models (this is equal to the path where model weights were stored at the end of training)')
    aa('--out_path', type=str,
        help='path where to store final nmf components matrix')
    aa('--n_components', type=int, nargs='+',
        help='list of component values to run grid search over')
    aa('--out_format', type=str,
        choices=['mat', 'txt', 'npy'],
        help='format in which to store nmf weights matrix to disk')
    aa('--save_all_components', action='store_true',
        help='whether to store all NMF weight matrices (i.e., corresponding to each number of latent components)')
    aa('--compare_nmfs', action='store_true',
        help='compare different NMF weight matrices against each other to test their reproducibility')
    aa('--rnd_seed', type=int, default=42,
        help='random seed for reproducibility')
    args = parser.parse_args()
    return args

def get_weights(in_path:str) -> List[np.ndarray]:
    weights = []
    for root, _, files in os.walk(in_path):
        for file in files:
            if file == 'weights_sorted.npy':
                with open(os.path.join(root, file), 'rb') as f:
                    W = utils.remove_zeros(np.load(f).T).T
                weights.append(W)
    return weights

def save_all_components_(out_path:str, W_nmfs:List[np.ndarray], n_components:List[float], file_format:str) -> None:
    for d, W_nmf in zip(n_components, W_nmfs):
        path = os.path.join(out_path, f'{d:02}')
        if not os.path.exists(path):
            os.makedirs(path)
        if file_format == 'txt':
            np.savetxt(os.path.join(path, f'nmf_components.txt'), W_nmf.T)
        elif file_format == 'npy':
            with open(os.path.join(path, f'nmf_components.npy'), 'wb') as f:
                np.save(f, W_nmf.T)
        else:
            scipy.io.savemat(os.path.join(path, f'nmf_components.mat'), {'components': W_nmf.T})

def save_argmax_components_(out_path:str, W_nmf:np.ndarray, file_format:str) -> None:
    if file_format == 'txt':
        np.savetxt(os.path.join(out_path, 'nmf_components.txt'), W_nmf)
    elif file_format == 'npy':
        with open(os.path.join(out_path, 'nmf_components.npy'), 'wb') as f:
            np.save(f, W_nmf)
    else:
        scipy.io.savemat(os.path.join(PATH, 'nmf_components.mat'), {'components': W_nmf})

def sort_dims_(W:np.ndarray) -> np.ndarray:
    return W[np.argsort(-np.linalg.norm(W, ord=1, axis=1))]

def correlate_nmf_components(Ws_nmf_i:list, Ws_nmf_j:list) -> List[Tuple[float]]:
    corrs = []
    rhos = np.arange(.7, .9, 0.5)
    for W_nmf_i, W_nmf_j in zip(Ws_nmf_i, Ws_nmf_j):
        corrs.append(tuple(cross_correlate_latent_dims([W_nmf_i, W_nmf_j], rho) for rho in rhos))
    return list(zip(*corrs)), rhos

def nmf_grid_search(
                    Ws_mu:List[np.ndarray],
                    n_components:List[int],
                    k_folds:int=5,
                    rnd_seed:int=42,
                    comparison:bool=False,
):
    np.random.seed(rnd_seed)
    kf = KFold(n_splits=k_folds, random_state=rnd_seed, shuffle=True)
    W_held_out = Ws_mu.pop(np.random.choice(len(Ws_mu)))
    X = np.concatenate(Ws_mu, axis=1)
    X = X[:, np.random.permutation(X.shape[1])]
    avg_r2_scores = np.zeros(len(n_components))
    W_nmfs = []
    for j, n_comp in enumerate(n_components):
        nmf = NMF(n_components=n_comp, init=None, max_iter=5000, random_state=rnd_seed)
        W_nmf = nmf.fit_transform(X)
        nnls_reg = LinearRegression(positive=True)
        r2_scores = np.zeros(int(k_folds))
        for k, (train_idx, test_idx) in enumerate(kf.split(W_nmf)):
            X_train, X_test = W_nmf[train_idx], W_nmf[test_idx]
            y_train, y_test = W_held_out[train_idx], W_held_out[test_idx]
            nnls_reg.fit(X_train, y_train)
            y_pred = nnls_reg.predict(X_test)
            r2_scores[k] = r2_score(y_test, y_pred)
        avg_r2_scores[j] = np.mean(r2_scores)
        W_nmfs.append(utils.remove_zeros(sort_dims_(W_nmf.T)))
    W_nmf_argmax = W_nmfs[np.argmax(avg_r2_scores)]
    return W_nmf_argmax.T, W_nmfs, avg_r2_scores

def aggregate_weights(
                      in_path:str,
                      out_path:str,
                      n_components:list,
                      out_format:str,
                      compare_nmfs:bool=False,
                      save_all_components:bool=False,
                      ) -> None:
    Ws = get_weights(in_path)
    Ws_copy = Ws[:]
    W_nmf_argmax, W_nmfs, mean_r2_scores = nmf_grid_search(Ws_copy, n_components=n_components)

    #make sure that output directory exists
    if not os.path.exists(out_path):
        os.makedirs(out_path)

    if compare_nmfs:
        _, W_nmfs_i, _ = nmf_grid_search(Ws[:len(Ws)//2], n_components=n_components, comparison=True)
        _, W_nmfs_j, _ = nmf_grid_search(Ws[len(Ws)//2:], n_components=n_components, comparison=True)
        correlations, rhos = correlate_nmf_components_(W_nmfs_i, W_nmfs_j)
        plot_nmf_correlations(out_path=out_path, correlations=correlations, thresholds=rhos, n_components=n_components)

    if save_all_components:
        save_all_components_(out_path=out_path, W_nmfs=W_nmfs, n_components=n_components, file_format=out_format)
    else:
        save_argmax_components_(out_path=out_path, W_nmf=W_nmf_argmax, file_format=out_format)
    #plot r2 scores as a function of the number of latent components
    plot_r2_scores(out_path=out_path, r2_scores=mean_r2_scores, n_components=n_components)

if __name__ == '__main__':
    #parse arguments and set random seeds
    args = parseargs()
    np.random.seed(args.rnd_seed)
    random.seed(args.rnd_seed)

    aggregate_weights(
                     in_path=args.in_path,
                     out_path=args.out_path,
                     n_components=args.n_components,
                     out_format=args.out_format,
                     compare_nmfs=args.compare_nmfs,
                     save_all_components=args.save_all_components,
    )