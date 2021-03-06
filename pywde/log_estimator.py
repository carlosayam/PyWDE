import math

# E ** (EGamma - 1)
# EGamma : Euler's gamma constant
CONST = 0.655219925816103568558124041375857597804832207694139862874

import math
import numpy as np
import itertools as itt
from .common import all_zs_tensor
from sklearn.neighbors import BallTree
from scipy.special import gamma, loggamma

from .pywt_ext import WaveletTensorProduct


def log_factorial(n):
    if n <= 1:
        return 0
    return np.log(np.array(range(2, n + 1))).sum()

def log_riemann_volume_class(k):
    "Total Riemannian volume in model class with k parameters"
    return math.log(2) + k/2 * math.log(math.pi) - loggamma(k/2)

def log_riemann_volume_param(k, n):
    "Riemannian volume around estimate with k parameters for n samples"
    return (k/2) * math.log(2 * math.pi / n)

class WaveletDensityEstimator(object):
    def __init__(self, waves, k=1, delta_j=0):
        """
        Builds a shape-preserving estimator based on square root and nearest neighbour distance.

        :param waves: wave specification for each dimension: List of (wave_name:str, j0:int)
        :param k: use k-th neighbour
        :param: delta_j: number of levels to go after j0 on the wavelet expansion part; 0 means no wavelet expansion,
            only scaling functions.
        """
        self.wave = WaveletTensorProduct([wave_desc[0] for wave_desc in waves])
        self.k = k
        self.jj0 = np.array([wave_desc[1] for wave_desc in waves])
        self.delta_j = delta_j
        self.pdf = None
        self.thresholding = None

    def _fitinit(self, xs):
        if self.wave.dim != xs.shape[1]:
            raise ValueError("Expected data with %d dimensions, got %d" % (self.wave.dim, xs.shape[1]))
        self.minx = np.amin(xs, axis=0)
        self.maxx = np.amax(xs, axis=0)
        self.n = xs.shape[0]
        self.calc_coefficients(xs)

    def fit(self, xs):
        "Fit estimator to data. xs is a numpy array of dimension n x d, n = samples, d = dimensions"
        self._fitinit(xs)
        self.pdf = self.calc_pdf()
        self.name = '%s, n=%d, j0=%s, Dj=%d' % (self.wave.name, self.n, str(self.jj0), self.delta_j)
        return True

    def calc_coefficients(self, xs):
        xs_balls = self.calculate_nearest_balls(xs)
        # print('NN', xs_balls)
        self.do_calculate(xs, xs_balls)

    def do_calculate(self, xs, xs_balls):
        self.coeffs = {}
        self.nums = {}
        qq = self.wave.qq
        self.norm_all = self.do_calculate_j(0, qq[0:1], xs, xs_balls)
        for j in range(self.delta_j):
            self.norm_all += self.do_calculate_j(j, qq[1:], xs, xs_balls)

    def do_calculate_j(self, j, qxs, xs, xs_balls):
        norm_j = 0.0
        if j not in self.coeffs:
            self.coeffs[j] = {}
            self.nums[j] = {}
        jj = self._jj(j)
        jpow2 = 2 ** jj
        for qx in qxs:
            zs_min, zs_max = self.wave.z_range('dual', (qx, jj, None), self.minx, self.maxx)
            self.coeffs[j][qx] = {}
            self.nums[j][qx] = {}
            for zs in itt.product(*all_zs_tensor(zs_min, zs_max)):
                num = self.wave.supp_ix('dual', (qx, jpow2, zs))(xs).sum()
                self.nums[j][qx][zs] = num
                v = (self.wave.fun_ix('dual', (qx, jpow2, zs))(xs) * xs_balls[:,0]).sum()
                self.coeffs[j][qx][zs] = v
                norm_j += v * v
        return norm_j

    def calc_pdf(self):
        def pdffun_j(coords, xs_sum, j, qxs, threshold):
            jj = self._jj(j)
            jpow2 = 2 ** jj
            norm_j = 0.0
            if self.thresholding and threshold:
                th_fun = self.thresholding
            else:
                th_fun = lambda n, j, dn, c: c
            for qx in qxs:
                for zs, coeff in self.coeffs[j][qx].items():
                    num = self.nums[j][qx][zs]
                    coeff_t = th_fun(self.n, j, num, coeff)
                    norm_j += coeff_t * coeff_t
                    vals = coeff_t * self.wave.fun_ix('base', (qx, jpow2, zs))(coords)
                    xs_sum += vals
            return norm_j
        def pdffun(coords):
            if type(coords) == tuple or type(coords) == list:
                xs_sum = np.zeros(coords[0].shape, dtype=np.float64)
            else:
                xs_sum = np.zeros(coords.shape[0], dtype=np.float64)
            qq = self.wave.qq
            norm_const = pdffun_j(coords, xs_sum, 0, qq[0:1], False)
            for j in range(self.delta_j):
                norm_const += pdffun_j(coords, xs_sum, j, qq[1:], True)
            return (xs_sum * xs_sum)/norm_const
        pdffun.dim = self.wave.dim
        return pdffun

    def _jj(self, j):
        return np.array([j0 + j for j0 in self.jj0])

    # factor for num samples n, dimension dim and nearest index k
    def calc_factor(self):
        v_unit = (np.pi ** (self.wave.dim / 2.0)) / gamma(self.wave.dim / 2.0 + 1)
        return math.sqrt(v_unit) * (gamma(self.k) / gamma(self.k + 0.5)) / math.sqrt(self.n)

    def calculate_nearest_balls(self, xs):
        ball_tree = BallTree(xs)
        k_near_radious = ball_tree.query(xs, self.k + 1)[0][:, [-1]]
        factor = self.calc_factor()
        return np.power(k_near_radious, self.wave.dim / 2.0) * factor

    def mdlfit(self, xs):
        self._fitinit(xs)
        best_tuple = self.calc_pdf_mdl(xs)
        self.thresholding = self.calc_hard_threshold_fun(best_tuple)
        self.pdf = self.calc_pdf()
        self.name = '%s, n=%d, j0=%s, Dj=%d [%s]' % (self.wave.name, self.n, str(self.jj0), self.delta_j, self.thresholding.__doc__)
        return True

    def calc_pdf_mdl(self, xs):
        qq = self.wave.qq
        num_alphas = len(ifpos(self.coeffs[0][qq[0]].values()))
        betas = []
        for j in range(self.delta_j):
            for qx in qq[1:]:
                for zs, v in self.coeffs[j][qx].items():
                    if math.fabs(v) == 0:
                        continue
                    num = self.nums[j][qx][zs]
                    # NOTE threshold formula inverted below
                    v_th = math.fabs(v) / math.sqrt(j + 1)
                    betas.append((j, qx, zs, v, v_th, num > GN_NUM)) ## num > GN_NUM
        betas.sort(key=lambda tt: (not tt[5], -tt[4]))
        # calculate log likelihood incrementally starting from alphas and then
        # adding 1 beta coefficient at a time
        if type(xs) == tuple or type(xs) == list:
            xs_sum = np.zeros(xs[0].shape, dtype=np.float64)
        else:
            xs_sum = np.zeros(xs.shape[0], dtype=np.float64)
        # alphas
        norm = 0.0
        jj = self._jj(0)
        jpow2 = 2 ** jj
        ranking = []
        for zs, coeff in self.coeffs[0][qq[0]].items():
            num = self.nums[0][qq[0]][zs]
            norm += coeff * coeff
            vals = coeff * self.wave.fun_ix('base', (qq[0], jpow2, zs))(xs)
            xs_sum += vals
        pdf_for_xs = (xs_sum * xs_sum)/norm
        logLL = - np.log(pdf_for_xs).sum()
        k = num_alphas
        penalty = log_riemann_volume_class(k) - log_riemann_volume_param(k, self.n)
        rank_tuple = (num_alphas, logLL, penalty, logLL + penalty, 0)
        best_tuple = rank_tuple
        #print(rank_tuple)
        ranking.append(rank_tuple)
        for a_beta in betas:
            j, qx, zs, coeff, th, num_gt = a_beta
            if not num_gt:
                continue
            jj = self._jj(j)
            jpow2 = 2 ** jj
            # NOTE we just pass the coefficient, i.e. it is hard thresholding
            norm += coeff * coeff
            vals = coeff * self.wave.fun_ix('base', (qx, jpow2, zs))(xs)
            xs_sum += vals
            # now sum
            pdf_for_xs = (xs_sum * xs_sum)/norm
            logLL = - np.log(pdf_for_xs).sum()
            k += 1
            penalty = log_riemann_volume_class(k) - log_riemann_volume_param(k, self.n)
            rank_tuple = (k, logLL, penalty, logLL + penalty, th)
            if rank_tuple[3] < best_tuple[3]:
                best_tuple = rank_tuple
            #print(rank_tuple)
            ranking.append(rank_tuple)
        self.ranking = ranking
        return best_tuple

    def mdl(self, xs):
        alphas, betas = self.k_range()
        best = (float('inf'), None, None)
        num_betas = len(betas)
        self.mld_data = {'k_betas': [], 'logLL': [], 'MLD_penalty': []}
        for betas_num, th_value in enumerate(betas):
            if th_value == 0.0:
                continue
            k = alphas + (num_betas - betas_num)
            penalty = log_riemann_volume_class(k) - log_riemann_volume_param(k, self.n)
            if penalty < 0:
                print('neg')
                continue
            self.thresholding = self.calc_hard_threshold_fun(betas_num, th_value)
            self.pdf = self.calc_pdf()
            logLL = - np.log(self.pdf(xs)).sum()
            val = logLL + penalty
            print(num_betas - betas_num, th_value, val, penalty)
            self.mld_data['k_betas'].append(num_betas - betas_num)
            self.mld_data['logLL'].append(logLL)
            self.mld_data['MLD_penalty'].append(penalty)
            if val < best[0]:
                best = (val, self.thresholding, self.pdf)
                print('>>>>', self.thresholding.__doc__, val)
        self.mld_best = best
        _, self.thresholding, self.pdf = best

    def k_range(self):
        "returns range of valid k (parameters) value"
        # it cannot be greater than number of samples
        # it cannot be greater than the number of coefficients
        qq = self.wave.qq
        alphas = len(ifpos(self.coeffs[0][qq[0]]))
        coeffs = []
        for j in range(self.delta_j):
            vs = itt.chain.from_iterable([self.coeffs[j][qx].values() for qx in qq[1:]])
            coeffs += [(math.fabs(value) / math.sqrt(j + 1)) for value in vs]
        return alphas, sorted(coeffs)

    def calc_hard_threshold_fun(self, best_tuple):
        betas_num = best_tuple[0]
        th_value = best_tuple[4]
        def hard_th(n, j, num, coeff):
            if num > GN_NUM:
                lvl_t = th_value * math.sqrt(j + 1)
            else:
                lvl_t = 0.0
            if coeff < 0:
                if -coeff < lvl_t:
                    return 0
                else:
                    return coeff
            else:
                if coeff < lvl_t:
                    return 0
                else:
                    return coeff
        hard_th.__doc__ = "Hard threshold at %g (index %d)" % (th_value, betas_num)
        return hard_th

GN_NUM = 35

def ifpos(vs):
    return [v for v in vs if math.fabs(v) > 0]