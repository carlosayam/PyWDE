import math
import numpy as np
import itertools as itt
from collections import namedtuple, OrderedDict
from .common import all_zs_tensor
from sklearn.neighbors import BallTree
from scipy.special import gamma
from datetime import datetime

from .pywt_ext import WaveletTensorProduct

ThresholdResult = namedtuple('ThresholdResult', ['threshold', 'pos_k', 'target_val', 'values', 'sorted_contrib', 'th_j', 'msg'])

# from scipy cookbook
def smooth(x, window_len=11, window='hanning'):
    """smooth the data using a window with requested size.

    This method is based on the convolution of a scaled window with the signal.
    The signal is prepared by introducing reflected copies of the signal
    (with the window size) in both ends so that transient parts are minimized
    in the begining and end part of the output signal.

    input:
        x: the input signal
        window_len: the dimension of the smoothing window; should be an odd integer
        window: the type of window from 'flat', 'hanning', 'hamming', 'bartlett', 'blackman'
            flat window will produce a moving average smoothing.

    output:
        the smoothed signal

    example:

    t=linspace(-2,2,0.1)
    x=sin(t)+randn(len(t))*0.1
    y=smooth(x)

    see also:

    numpy.hanning, numpy.hamming, numpy.bartlett, numpy.blackman, numpy.convolve
    scipy.signal.lfilter

    TODO: the window parameter could be the window itself if an array instead of a string
    NOTE: length(output) != length(input), to correct this: return y[(window_len/2-1):-(window_len/2)] instead of just y.
    """

    if x.ndim != 1:
        raise ValueError("smooth only accepts 1 dimension arrays.")

    if x.size < window_len:
        raise ValueError("Input vector needs to be bigger than window size.")

    if window_len < 3:
        return x

    if not window in ['flat', 'hanning', 'hamming', 'bartlett', 'blackman']:
        raise ValueError("Window is on of 'flat', 'hanning', 'hamming', 'bartlett', 'blackman'")

    s = np.r_[x[window_len - 1:0:-1], x, x[-2:-window_len - 1:-1]]
    # print(len(s))
    if window == 'flat':  # moving average
        w = np.ones(window_len, 'd')
    else:
        w = getattr(np, window)(window_len)

    y = np.convolve(w / w.sum(), s, mode='valid')
    return y

class WParams(object):
    def __init__(self, wde, with_betas=True):
        self.k = wde.k
        self.wave = wde.wave
        self.jj0 = wde.jj0
        self.delta_j = wde.delta_j
        self.coeffs = {}
        self.minx = wde.minx
        self.maxx = wde.maxx
        self._calc_indexes(with_betas)
        self.n = 0
        self.pdf = None
        self.test = None
        self.ball_tree = None
        self.xs_balls = None
        self.xs_balls_inx = None

    def to_dict(self):
        return dict(
            k=self.k,
            wave=self.wave.to_dict(),
            jj0=self.jj0.tolist(),
            delta_j=self.delta_j,
            coeffs=self.coeffs,
        )

    @staticmethod
    def from_dict(a_dict):
        return None

    def pre_coeffs(self, xs):
        self.n = xs.shape[0]
        self.ball_tree = BallTree(xs)
        self.calculate_nearest_balls(xs)

    def calc_coeffs(self, xs):
        norm = 0.0
        omega = self.omega(self.n)
        remove = []
        for key in self.coeffs.keys():
            if self.coeffs[key] is not None:
                continue
            j, qx, zs, jpow2 = key
            jpow2 = np.array(jpow2)
            num = self.wave.supp_ix('dual', (qx, jpow2, zs))(xs).sum()
            terms_d = self.wave.fun_ix('dual', (qx, jpow2, zs))(xs)
            terms_b = self.wave.fun_ix('base', (qx, jpow2, zs))(xs)
            coeff = (terms_d * self.xs_balls).sum() * omega
            coeff_b = (terms_b * self.xs_balls).sum() * omega
            #print('beta_{%s}' % str(key), '=', coeff)
            if math.fabs(coeff) < 1.0e-7 and math.fabs(coeff_b) < 1.0e-7:
                remove.append(key)
                continue
            self.coeffs[key] = (coeff, coeff_b, num)
            norm += coeff * coeff_b
        for key in remove:
            del self.coeffs[key]
        print('calc_coeffs #', len(self.coeffs), norm)

    def calc_coeffs_loo(self, xs, ix_loo):
        norm = 0.0
        omega = self.omega(self.n - 1)
        remove = []
        xs = np.delete(xs, ix_loo, axis=0)
        balls = []
        for i in range(self.xs_balls_inx.shape[0]):
            if i == ix_loo:
                continue
            if self.xs_balls_inx[i, -2] == ix_loo:
                balls.append(self.xs_balls2[i])
            else:
                balls.append(self.xs_balls[i])
        balls = np.array(balls)
        for key in self.coeffs.keys():
            if self.coeffs[key] is not None:
                continue
            j, qx, zs, jpow2 = key
            jpow2 = np.array(jpow2)
            num = self.wave.supp_ix('dual', (qx, jpow2, zs))(xs).sum()
            terms_d = self.wave.fun_ix('dual', (qx, jpow2, zs))(xs)
            terms_b = self.wave.fun_ix('base', (qx, jpow2, zs))(xs)
            coeff = (terms_d * balls).sum() * omega
            coeff_b = (terms_b * balls).sum() * omega
            #print('beta_{%s}' % str(key), '=', coeff)
            if math.fabs(coeff) < 1.0e-7 and math.fabs(coeff_b) < 1.0e-7:
                remove.append(key)
                continue
            self.coeffs[key] = (coeff, coeff_b, num)
            norm += coeff * coeff_b
        for key in remove:
            del self.coeffs[key]
        # print('calc_coeffs #', len(self.coeffs), norm)


    def calc_pdf(self, coeffs):
        def fun(coords):
            xs_sum = self.xs_sum_zeros(coords)
            for key in coeffs.keys():
                j, qx, zs, jpow2 = key
                jpow2 = np.array(jpow2)
                coeff, coeff_b, num = coeffs[key]
                vals = coeff * self.wave.fun_ix('base', (qx, jpow2, zs))(coords)
                xs_sum += vals
            return (xs_sum * xs_sum) / fun.norm_const

        def to_dict(a_fun):
            return {'coeffs': coeffs}

        fun.norm_const = sum([coeff * coeff_b for coeff, coeff_b, num in coeffs.values()])
        fun.dim = self.wave.dim
        fun.nparams = len(coeffs)
        fun.to_dict = to_dict
        min_num = min([num for coeff, coeff_b, num in coeffs.values() if num > 0])
        # print('>> WDE PDF')
        # print('Num coeffs', len(coeffs))
        # print('Norm', fun.norm_const)
        # print('min num', min_num)
        return fun

    def gen_pdf(self, xs_sum, coeffs_items, coords):
        norm_const = 0.0
        for key, tup in coeffs_items:
            j, qx, zs, jpow2 = key
            jpow2 = np.array(jpow2)
            coeff, coeff_b, num = tup
            vals = coeff * self.wave.fun_ix('base', (qx, jpow2, zs))(coords)
            norm_const += coeff * coeff_b
            xs_sum += vals
            yield key, (xs_sum * xs_sum) / norm_const

    def calc_terms(self, key, coeff, coeff_b, xs):
        # see paper, Q definition
        if self.xs_balls_inx is None:
            raise ValueError('Use calc_coeffs first')

        j, qx, zs, jpow2 = key
        jpow2 = np.array(jpow2)

        # import code
        # code.interact('calc_terms **', local=locals())

        fun_i_dual = self.wave.fun_ix('dual', (qx, jpow2, zs))(xs)
        fun_i_base = self.wave.fun_ix('base', (qx, jpow2, zs))(xs)
        omega_n = self.omega(self.n)
        omega_n1 = self.omega(self.n-1)
        omega_n2 = omega_n * omega_n1

        term1 = omega_n1 / omega_n * coeff * coeff_b

        term2 = omega_n2 * (fun_i_dual * fun_i_base * self.xs_balls * self.xs_balls).sum()

        vals_i = np.zeros(self.n)
        obj = self
        for j in range(self.n):
            psi_j = fun_i_dual[j]
            deltaV_j = self.xs_balls2[j] - self.xs_balls[j]
            # TODO: what if just do k = self.k ?? (or even k=1) ??
            ## for k in [self.k - 1]:
            for k in range(self.k):
                i = self.xs_balls_inx[j, k+1] # position 0 is x_j
                psi_i = fun_i_base[i]
                v1_i = self.xs_balls[i]
                v = psi_i * psi_j * v1_i * deltaV_j
                vals_i[i] += v
        term3 = omega_n2 * vals_i.sum()
        return term1, term2, term3, coeff * coeff_b

    def calc1(self, key, tup, coords, norm):
        j, qx, zs, jpow2 = key
        jpow2 = np.array(jpow2)
        coeff, num = tup
        vals = coeff * self.wave.fun_ix('base', (qx, jpow2, zs))(coords)
        return vals, norm + coeff*coeff

    def xs_sum_zeros(self, coords):
        if type(coords) == tuple or type(coords) == list:
            xs_sum = np.zeros(coords[0].shape, dtype=np.float64)
        else:
            xs_sum = np.zeros(coords.shape[0], dtype=np.float64)
        return xs_sum

    def _calc_indexes(self, with_betas, cur_j=0):
        qq = self.wave.qq
        print('\n')
        alphas = self._calc_indexes_j(cur_j, qq[0:1])
        print('# alphas =', alphas)
        if not with_betas:
            return
        for j in range(self.delta_j):
            betas = self._calc_indexes_j(j, qq[1:])
            print('# coeffs %d =' % j, betas)

    def calc_indexes_j(self, j):
        betas = self._calc_indexes_j(j, self.wave.qq[1:])
        print("# calc'ed beta coeffs %d =" % j, betas)

    def _calc_indexes_j(self, j, qxs):
        jj = self._jj(j)
        jpow2 = tuple(2.0 ** jj)
        ## print('-->', jpow2)
        ncoeff = 0
        for qx in qxs:
            zs_min_d, zs_max_d = self.wave.z_range('dual', (qx, jpow2, None), self.minx, self.maxx)
            zs_min_b, zs_max_b = self.wave.z_range('base', (qx, jpow2, None), self.minx, self.maxx)
            zs_min = np.min((zs_min_d, zs_min_b), axis=0)
            zs_max = np.max((zs_max_d, zs_max_b), axis=0)
            for zs in itt.product(*all_zs_tensor(zs_min, zs_max)):
                if (j, qx, zs, jpow2) not in self.coeffs:
                    self.coeffs[(j, qx, zs, jpow2)] = None
                    ncoeff += 1
        return ncoeff

    def sqrt_vunit(self):
        "Volume of unit hypersphere in d dimensions"
        return (np.pi ** (self.wave.dim / 4.0)) / (gamma(self.wave.dim / 2.0 + 1) ** 0.5)

    def omega(self, n):
        "Bias correction for k-th nearest neighbours sum for sample size n"
        ## return gamma(self.k) / gamma(self.k + 0.5) / math.sqrt(n)
        return math.sqrt(n - 1) * gamma(self.k) / gamma(self.k + 0.5) / n

    def calculate_nearest_balls(self, xs):
        "Calculate and store (k+1)-th nearest balls"
        ix = -2
        dist, inx = self.ball_tree.query(xs, self.k + 2)
        k_near_radious = dist[:, -2:]
        xs_balls = np.power(k_near_radious, self.wave.dim / 2.0)
        self.xs_balls = xs_balls[:, 0] * self.sqrt_vunit()
        self.xs_balls2 = xs_balls[:, 1] * self.sqrt_vunit()
        self.xs_balls_inx = inx

    def _jj(self, j):
        return np.array([j0 + j for j0 in self.jj0])


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
        self.wave_series = None
        self.pdf = None
        self.thresholding = None
        self.params = None
        self._xs = None

    def _fitinit(self, xs):
        if self._xs is xs:
            # objec ref comparisson, do not recalc if already calculated
            self.params = self._params
            return
        if self.wave.dim != xs.shape[1]:
            raise ValueError("Expected data with %d dimensions, got %d" % (self.wave.dim, xs.shape[1]))
        self.minx = np.amin(xs, axis=0)
        self.maxx = np.amax(xs, axis=0)
        self.params = WParams(self)
        self.params.pre_coeffs(xs)
        self.params.calc_coeffs(xs)
        self._xs = xs
        self._params = self.params

    def to_dict(self, fname):
        data = dict(
            wave=self.wave.to_dict(),
            k=self.k,
            jj0=self.jj0.tolist(),
            delta_j=self.delta_j,
            params=self.params.as_dict(),
            _xs=self._xs.tolist(),
            minx=self.minx.tolist(),
            maxx=self.maxx.tolist(),
            name=self.name,
        )
        return data

    def from_dict(self):
        pass

    def fit(self, xs):
        "Fit estimator to data. xs is a numpy array of dimension n x d, n = samples, d = dimensions"
        print('Regular estimator')
        t0 = datetime.now()
        self._fitinit(xs)
        self.pdf = self.params.calc_pdf(self.params.coeffs)
        self.name = '%s N=%d j0=%s Dj=%d k=%d #par=%d Full' % (self.wave.name, self.params.n, str(self.jj0),
                                                               self.delta_j, self.k, len(self.params.coeffs))
        print('secs=', (datetime.now() - t0).total_seconds())

    def _iterinit(self, xs):
        if self._xs is xs:
            # objec ref comparisson, do not recalc if already calculated
            self.params = self._params
            return
        if self.wave.dim != xs.shape[1]:
            raise ValueError("Expected data with %d dimensions, got %d" % (self.wave.dim, xs.shape[1]))
        self.minx = np.amin(xs, axis=0)
        self.maxx = np.amax(xs, axis=0)
        self.params = WParams(self, with_betas=False)
        self.params.pre_coeffs(xs)
        self.params.calc_coeffs(xs)
        self._xs = xs
        self._params = self.params

    def iterfit(self, xs):
        print('Iter estimator: Normed, iterated')
        t0 = datetime.now()
        self._iterinit(xs)
        coeffs = self.calc_iter_pdf(xs)
        self.pdf = self.params.calc_pdf(coeffs)
        self.name = '%s N=%d j0=%s Dj=%d k=%d #par=%d Iter' % (self.wave.name, self.params.n, str(self.jj0),
                                                               self.delta_j, self.k, len(coeffs))
        print('secs=', (datetime.now() - t0).total_seconds())


    def cvfit(self, xs, loss, ordering, is_single=True):
        "options = dict(loss=?, ordering=?)"
        if loss not in WaveletDensityEstimator.LOSSES:
            raise ValueError('Wrong loss')
        if ordering not in WaveletDensityEstimator.ORDERINGS:
            raise ValueError('Wrong ordering')
        print('CV estimator: %s, %s; single %s' % (loss, ordering, str(is_single)))
        t0 = datetime.now()
        self._fitinit(xs)
        coeffs = self.calc_pdf_cv(xs, loss, ordering, is_single)
        self.pdf = self.params.calc_pdf(coeffs)
        self.name = '%s N=%d j0=%s Dj=%d k=%d #par=%d Lss=%s Thr=%s M=%s' % (self.wave.name, self.params.n,
                                                                        str(self.jj0), self.delta_j, self.k, len(coeffs),
                                                                        loss[:3], ordering[:4], str(not is_single)[0])
        print('secs=', (datetime.now() - t0).total_seconds())

    Q_ORD = 'QTerm'
    AQ_ORD = 'AQTerm'
    Q2_ORD = 'Q2Term'
    AQ2_ORD = 'AQ2Term'
    I_ORD = 'QImpr'
    AI_ORD = 'AQImpr'
    T_ORD = 'Trad'
    T2_ORD = 'TrBio'
    RT_ORD = "RefTao"
    RTA_ORD = "RefAllTao"
    # these _orderings_ reflect different threshold strategies;
    # note we use the negative values, so it is descending order
    ORDERINGS = OrderedDict([
        (Q_ORD, (lambda coeff, coeff2, contrib, j : contrib)),
        (AQ_ORD, (lambda coeff, coeff2, contrib, j : math.fabs(contrib))),
        # what is this?! it seems to work to restrict noise from higher levels!
        (Q2_ORD, (lambda coeff, coeff2, contrib, j : coeff2 + (contrib - coeff2))),
        (AQ2_ORD, (lambda coeff, coeff2, contrib, j: math.fabs(coeff2 + (contrib - coeff2)))),
        (I_ORD, (lambda coeff, coeff2, contrib, j : (contrib - 0.5 * coeff2))),
        (AI_ORD, (lambda coeff, coeff2, contrib, j : math.fabs(contrib - 0.5 * coeff2))),
        (T_ORD, (lambda coeff, coeff2, contrib, j : math.fabs(coeff) / math.sqrt(j + 1))),
        (T2_ORD, (lambda coeff, coeff2, contrib, j : coeff2 / (j + 1))),
    ])

    NEW_LOSS = 'Improved'
    ORIGINAL_LOSS = 'Original'
    NORMED_LOSS = 'Normed'
    LOSSES = [NEW_LOSS, ORIGINAL_LOSS, NORMED_LOSS]

    @staticmethod
    def valid_options(is_single):
        for loss in WaveletDensityEstimator.LOSSES:
            for ordering in WaveletDensityEstimator.ORDERINGS.keys():
                yield loss, ordering, is_single

    def best_j(self, xs):
        print("Let's rock Best J")
        t0 = datetime.now()
        if self.wave.dim != xs.shape[1]:
            raise ValueError("Expected data with %d dimensions, got %d" % (self.wave.dim, xs.shape[1]))
        self.minx = np.amin(xs, axis=0)
        self.maxx = np.amax(xs, axis=0)
        self.params = WParams(self, with_betas=False)
        # self.params.pre_coeffs but w/ xtest
        # self.params.pre_coeffs(xs_test), kinda
        self.params.n = xs.shape[0]
        self.params.ball_tree = BallTree(xs)
        self.params.calculate_nearest_balls(xs)
        balls = self.params.xs_balls
        omega = self.params.omega(self.params.n)
        # self.params_pre_coeffs(xs)
        self._xs = xs
        self._params = self.params
        coeffs = {}
        alpha_contribution = 0.0
        alpha_norm = 0.0
        # initially, just alphas
        ini_j = 0
        while True:
            vals = []
            self.params.coeffs = {}
            self.params._calc_indexes_j(ini_j, self.params.wave.qq[0:1])
            print('J', ini_j)
            for ix, x in enumerate(xs):
                # this is inefficient according to draft; uses direct logic
                self.params.calc_coeffs_loo(xs, ix)
                pdf = self.params.calc_pdf(self.params.coeffs)
                b_hat_x = np.sqrt(pdf(np.array([x])))
                print(x, '=>', b_hat_x)
                vals.append(b_hat_x[0])
            b_hat = omega * (np.array(vals) * balls).sum()
            print('LEVEL => [[', ini_j, ']], b_hat =', b_hat, '# coeffs :', len(self.params.coeffs))
            ini_j += 1
            if ini_j > 8:
                print('Done')
                break
        print('secs=', (datetime.now() - t0).total_seconds())

    def calc_iter_pdf(self, xs):
        coeffs = {}
        alpha_contribution = 0.0
        alpha_norm = 0.0
        # initially, just alphas
        ini_j = 0
        while True:
            for key, tup in self.params.coeffs.items():
                coeff, coeff_b, num = tup
                if coeff == 0.0:
                    continue
                term1, term2, term3, coeff2 = self.params.calc_terms(key, coeff, coeff_b, xs)
                coeff_contribution = term1 - term2 + term3
                coeffs[key] = tup
                alpha_norm += coeff2
                alpha_contribution += coeff_contribution
            loss = 1 - alpha_contribution / math.sqrt(alpha_norm)
            print(ini_j, ', loss =', loss, ' loss^2 =', loss ** 2)
            break
            ini_j += 1
            if ini_j > 8:
                print('Done')
                import sys
                sys.exit()
            self.params.coeffs = {}
            self.params._calc_indexes_j(ini_j, self.params.wave.qq[0:1])
            self.params.calc_coeffs(xs)


        print('Wish me luck!')
        contribution = alpha_contribution
        norm2 = alpha_norm
        all_possible = 0
        all_rejected = 0
        samples_perc = 0.99
        trace_v = []
        thr = 0.5 / self.params.n
        new_betas = 0
        lvl_j = 0
        max_lvl = 0
        while True:
            print('\n** Explore j =', lvl_j, 'loss =', loss, '#params =', len(coeffs))
            for dj in range(self.delta_j):
                self.params.calc_indexes_j(lvl_j + dj)
            self.params.calc_coeffs(xs)
            contributions = []
            for key, tup in self.params.coeffs.items():
                coeff, coeff_b, num = tup
                if coeff == 0.0:
                    continue
                j, qx, zs, jpow2 = key
                is_alpha = j == 0 and all([qi == 0 for qi in qx])
                if is_alpha or j < lvl_j:
                    continue
                # TODO - add only of level before has X% coefficients
                term1, term2, term3, coeff2 = self.params.calc_terms(key, coeff, coeff_b, xs)
                coeff_contribution = term1 - term2 + term3
                coeff_norm2 = coeff2
                contributions.append((key, coeff_contribution, coeff_norm2, tup))
            print('done contribution factors #', len(contributions))
            all_possible += len(contributions)
            improved = False
            stop = False
            while True:
                if len(contributions) == 0:
                    print('Beta level', lvl_j, 'exhausted')
                    break
                # stopping rule A: certain number for coeff per sample
                if len(coeffs) >= self.params.n * samples_perc:
                    print('Stopping because too many coefficients')
                    stop = True
                    break
                # 1 - (M + a)/sqrt(N + b) < 1 - M/sqrt(N)
                # (M + a)/sqrt(N + b) - M/sqrt(N) > 0
                vals = np.array([
                    (contribution + coeff_contribution)/math.sqrt(norm2 + coeff_norm2) - contribution/math.sqrt(norm2)
                    for _, coeff_contribution, coeff_norm2, _ in contributions
                ])
                ix = np.argmax(vals)
                new_loss = 1 - (contribution + contributions[ix][1])/math.sqrt(norm2 + contributions[ix][2])
                # new_loss += contributions[-1][2] * 0.5
                # print(loss,'--',ix, new_loss, loss-new_loss, contributions[ix][2], contributions[ix][2] - (loss - new_loss))
                if 0 < new_loss < loss: ## or new_betas < 2:
                    # below based on inspection - but looking into MDL paper to see if it can be justified
                    #if new_betas < 5: # or contributions[ix][2] - (loss - new_loss) > thr:
                        ## print(ix, new_loss, ';', contributions[ix][1], contributions[ix][2], contributions[ix][1]/math.sqrt(contributions[ix][2]))
                        improved = True
                        new_betas += 1
                        the_j = contributions[ix][0][0]
                        max_lvl = max(max_lvl, the_j)
                        trace_v.append((len(trace_v), new_loss, loss, contributions[ix][2], the_j))
                        contribution += contributions[ix][1]
                        norm2 += contributions[ix][2]
                        coeffs[contributions[ix][0]] = contributions[ix][3]
                        # print('improvement', new_loss, '<', loss, ' at ', ix, ' for contributions', contributions[ix])
                        loss = new_loss
                        del contributions[ix]
                        continue
                all_rejected += len(contributions)
                print('Beta level', lvl_j, 'finished. Coeffs left', len(contributions), ' f=', all_rejected / all_possible)
                break
            # stopping rule B: rejecting too many across levels
            if not improved or loss < thr / 2: # or all_rejected /  all_possible > 0.90: ## (!)
                break
            if stop:
                break
            lvl_j += 1
            break
        self.trace_v = np.array(trace_v)
        # true delta_j
        self.delta_j = max_lvl+1
        print('Iterative: params =', len(coeffs), 'loss =', loss, 'delta-j (levels) =', self.delta_j)
        return coeffs

    def calc_pdf_cv(self, xs, loss, ordering, single_threshold=True):
        if self.delta_j == 0:
            raise ValueError('Cannot do CV over alpha only ... yet')
        if ordering == WaveletDensityEstimator.RT_ORD:
            return self.calc_pdf_ref_tao(xs, loss)
        coeffs = {}
        alpha_contributions = []
        contributions = []
        alpha_contribution = 0.0
        alpha_norm = 0.0
        fun = WaveletDensityEstimator.ORDERINGS[ordering]
        for key, tup in self.params.coeffs.items():
            coeff, coeff_b, num = tup
            if coeff == 0.0:
                continue
            j, qx, zs, jpow2 = key
            is_alpha = j == 0 and all([qi == 0 for qi in qx])
            term1, term2, term3, coeff2 = self.params.calc_terms(key, coeff, coeff_b, xs)
            coeff_contribution = term1 - term2 + term3
            if is_alpha:
                if True: #coeff_contribution > 0:
                    coeffs[key] = tup
                    alpha_norm += coeff2
                    alpha_contribution += coeff_contribution
                    # TODO: alpha could be filtered for Q > 0 (!!)
                    # threshold = fun(coeff, coeff2, coeff_contribution, j)
                    # alpha_contributions.append(((key, tup), threshold, (term1, term2, term3, coeff2)))
                continue

            # threshold is the order-by number; here the options
            threshold = fun(coeff, coeff2, coeff_contribution, j)
            contributions.append(((key, tup), threshold, (term1, term2, term3, coeff2)))

        print('alphas =', len(coeffs))

        if single_threshold:
            self.threshold = self.single_threshold(coeffs, loss, contributions, alpha_norm, alpha_contribution)
            for values in self.threshold.sorted_contrib[:self.threshold.pos_k + 1]:
                key, tup = values[0]
                coeffs[key] = tup
            return coeffs

        threshold = self.single_threshold(coeffs, loss, contributions, alpha_norm, alpha_contribution)

        threshold_c = threshold.threshold
        self.thresholds = self.multi_threshold(coeffs, loss, threshold.sorted_contrib, alpha_norm, alpha_contribution,
                                               threshold_c, threshold.th_j, threshold.target_val)
        for a_threshold in self.thresholds:
            for values in a_threshold.sorted_contrib[:a_threshold.pos_k + 1]:
                key, tup = values[0]
                coeffs[key] = tup
        print('Multi-threshold: params =', len(coeffs))
        return coeffs

    class StateJ:
        def __init__(self, j, contribs, threshold_c):
            self.j = j
            self.contribs = contribs
            self.len_contribs = len(contribs)
            self.curr_k = np.argmin(np.array([v[1] for v in self.contribs]) >= threshold_c) - 1
            batta_sum = np.array([(term1 - term2 + term3) for _, _, (term1, term2, term3, _) in self.contribs])
            self.batta_sums = np.cumsum(batta_sum)
            norm_sum = np.array([coeff2 for _, _, (_, _, _, coeff2) in self.contribs])
            self.norm_sums = np.cumsum(norm_sum)
            self.dk = 0

        def dk_ok(self):
            new_k = self.curr_k + self.dk
            return 0 <= new_k < self.len_contribs

        def set_new(self):
            self.curr_k += self.dk

        def as_result(self, target_val):
            # 'threshold', 'pos_k', 'target_val', 'values', 'sorted_kept', 'msg'
            return ThresholdResult(
                self.contribs[self.curr_k][1],
                self.curr_k,
                target_val,
                None,
                self.contribs,
                self.j,
                'Multi'
            )

        def batta_sum(self):
            if not self.dk_ok():
                return 0.0
            return self.batta_sums[self.curr_k + self.dk]

        def norm_sum(self):
            if not self.dk_ok():
                return 0.0
            return self.norm_sums[self.curr_k + self.dk]


    class StateWDE:
        def __init__(self, loss, alpha_norm, alpha_contrib):
            self.levels = []
            self.alpha_norm = alpha_norm
            self.alpha_contrib = alpha_contrib
            if loss == WaveletDensityEstimator.ORIGINAL_LOSS:
                self.lossfn = lambda batta_sum, norm_sum : 1 - batta_sum
            elif loss == WaveletDensityEstimator.NORMED_LOSS:
                self.lossfn = lambda batta_sum, norm_sum : 1 - batta_sum / math.sqrt(norm_sum)
            elif loss == WaveletDensityEstimator.NEW_LOSS:
                self.lossfn = lambda batta_sum, norm_sum :  0.5 + 0.5 * norm_sum - batta_sum

        def append(self, state_j):
            self.levels.append(state_j)

        def eval_target(self):
            batta_sum = self.alpha_contrib
            norm_sum = self.alpha_norm
            for state_j in self.levels:
                batta_sum += state_j.batta_sum()
                norm_sum += state_j.norm_sum()
            return self.lossfn(batta_sum, norm_sum)

        def curr_ks(self):
            return [state_j.curr_k for state_j in self.levels]

        def curr_cs(self):
            return [state_j.contribs[state_j.curr_k][1] for state_j in self.levels]


    def multi_threshold(self, coeffs, loss, sorted_contributions, alpha_norm, alpha_contribution, threshold_c, th_j, loss_val):
        # determine current posk for each level
        # contributions = list of (key, tup), threshold, (term1, term2, term3, coeff2)
        # key is j, qx, zs, jpow2
        state_wde = WaveletDensityEstimator.StateWDE(loss, alpha_norm, alpha_contribution)
        for j in range(self.delta_j):
            contrib_j = list(filter(lambda tup: tup[0][0][0] == j, sorted_contributions))
            contrib_j = sorted(contrib_j, key=lambda values: -values[1])
            if j < th_j:
                threshold_c = float('-inf')
            elif j == th_j:
                threshold_c = threshold_c
            else:
                threshold_c = float('inf')
            state_j = WaveletDensityEstimator.StateJ(j, contrib_j, threshold_c)
            state_wde.append(state_j)

        ok = True
        current_val = state_wde.eval_target()
        trace = [(state_wde.curr_ks(), current_val, state_wde.curr_cs())]
        print('> done setting up state - multi')
        print('> current_val, %f (orig %f)' % (current_val, loss_val))
        print('> ks ', state_wde.curr_ks())
        print('> thresholds', state_wde.curr_cs())
        ddx = 2

        # TODO, there is a minor tiny difference between this current_val and the result from single threshold (?!)

        while ok:
            ok = False
            best_dk_js = None
            # print(trace[-1])

            for dk_js in itt.product([-ddx, 0, ddx], repeat=self.delta_j):
                if all([dk_j == 0 for dk_j in dk_js]):
                    # omit if all pos_k are going to be the same
                    continue
                # set dk for all levels
                for j in range(self.delta_j):
                    state_wde.levels[j].dk = dk_js[j]
                if not all([state_wde.levels[j].dk_ok() for j in range(self.delta_j)]):
                    # omit if we go beyond any of the levels
                    continue
                # recalc sums for new positions
                new_val = state_wde.eval_target()
                #
                # Intermediate values on the grid
                #  print(dk_js, '>', new_val)
                #
                if new_val < current_val:
                    ok = True
                    current_val = new_val
                    best_dk_js = dk_js
            # if we managed to improve, adjust current values for k, qsum and norm for each level
            # for next iteration
            if ok:
                print('>> improved @', best_dk_js, current_val)
                for j in range(self.delta_j):
                    state_wde.levels[j].dk = best_dk_js[j] / ddx
                    state_wde.levels[j].set_new()
                trace.append((state_wde.curr_ks(), current_val, state_wde.curr_cs()))
        thresholds = [state_j.as_result(current_val) for state_j in state_wde.levels]
        return thresholds

    def single_threshold(self, coeffs, loss, contributions, alpha_norm, alpha_contribution):
        vals, sorted_cont = self.calc_c_function(contributions, alpha_norm, alpha_contribution, loss, len(coeffs))
        if len(vals) == 0:
            return ThresholdResult(0, -1, 1.0, np.array([]), np.array([]), -1, 'No betas')
        # import code
        # code.interact('** %s' % ','.join(locals().keys()), local=locals())
        k = self.pos_k_min(vals, self.params.n - len(coeffs))
        threshold, target_val, th_j = vals[k,:]
        print('single threshold', len(sorted_cont), '=> (at %d)' % k, 'C =', threshold, 'J =', th_j, 'V =',target_val, '(%s)' % loss)
        return ThresholdResult(threshold, k, target_val, vals, sorted_cont, th_j, 'All')

    def calc_c_function(self, contributions, curr_norm, curr_contribution, loss, alpha_nump):
        ## contributions = sorted(contributions, key=lambda values: -values[1]) ## <-- original
        contributions = sorted(contributions, key=lambda values: (values[0][0][0], -values[1])) ## j first
        print('curr_norm, curr_contribution =', curr_norm, curr_contribution)

        batta_sum = curr_contribution
        norm_sum = curr_norm
        vals = []
        for nump, values in enumerate(contributions):
            j = values[0][0][0]
            threshold = values[1]
            term1, term2, term3, coeff2 = values[2]
            norm_sum += coeff2
            coeff_contribution = term1 - term2 + term3
            batta_sum += coeff_contribution
            if loss == WaveletDensityEstimator.ORIGINAL_LOSS:
                target = 1 - batta_sum
            elif loss == WaveletDensityEstimator.NORMED_LOSS:
                target = 1 - batta_sum / math.sqrt(norm_sum)
            elif loss == WaveletDensityEstimator.NEW_LOSS:
                target = 0.5 + 0.5 * norm_sum - batta_sum
            else:
                raise ValueError('Unknown loss=%s' % loss)

            # MDL correction (?) - how do u justify this?
            ## BELOW - not justified, completly "heuristic" - one could try to see if related to
            ## 2017, Peter, Rangarajan, Moyou - The Geometry of Orthogonal-Series, Square-Root Density Estimators,
            ##       Applications in Computer Vision and Model Selection.pdf

            # pp = alpha_nump + nump
            # target += math.log(math.log(pp) + pp / 2 * math.log(math.pi)
            #                    - pp / 2 * (math.log(2 * math.pi) - math.log(self.params.n))) / math.log(self.params.n)

            ## Another alternative is to penalise based on J (we have math.sqrt(j) in a threshold calculation
            ## but, again, what would be the justification for that ... variance considerations? where?

            vals.append((threshold, target, j))
        return np.array(vals), contributions

    class StateTaoJ:
        pass

    def calc_pdf_ref_tao(self, xs, loss, ordering):
        coeffs = {}
        for j in range(self.delta_j):
            # TODO - think of something that can be done as refinement
            # 1. refinement equation (new alphas) vs their variances
            pass
        alpha_contributions = []
        contributions = []
        alpha_contribution = 0.0
        alpha_norm = 0.0
        fun = WaveletDensityEstimator.ORDERINGS[ordering]
        for key, tup in self.params.coeffs.items():
            coeff, coeff_b, num = tup
            if coeff == 0.0:
                continue
            j, qx, zs, jpow2 = key
            is_alpha = j == 0 and all([qi == 0 for qi in qx])
            term1, term2, term3, coeff2 = self.params.calc_terms(key, coeff, coeff_b, xs)
            coeff_contribution = term1 - term2 + term3
            if is_alpha:
                if True: #coeff_contribution > 0:
                    coeffs[key] = tup
                    alpha_norm += coeff2
                    alpha_contribution += coeff_contribution
                    # TODO: alpha could be filtered for Q > 0 (!!)
                    # threshold = fun(coeff, coeff2, coeff_contribution, j)
                    # alpha_contributions.append(((key, tup), threshold, (term1, term2, term3, coeff2)))
                continue

            # threshold is the order-by number; here the options
            threshold = fun(coeff, coeff2, coeff_contribution, j)
            contributions.append(((key, tup), threshold, (term1, term2, term3, coeff2)))

        print('alphas =', len(coeffs))

        if single_threshold:
            self.threshold = self.single_threshold(coeffs, loss, contributions, alpha_norm, alpha_contribution)
            for values in self.threshold.sorted_contrib[:self.threshold.pos_k + 1]:
                key, tup = values[0]
                coeffs[key] = tup
            return coeffs

        threshold = self.single_threshold(coeffs, loss, contributions, alpha_norm, alpha_contribution)

        threshold_c = threshold.threshold
        self.thresholds = self.multi_threshold(coeffs, loss, threshold.sorted_contrib, alpha_norm, alpha_contribution,
                                               threshold_c, threshold.target_val)
        for a_threshold in self.thresholds:
            for values in a_threshold.sorted_contrib[:a_threshold.pos_k + 1]:
                key, tup = values[0]
                coeffs[key] = tup
        print('Multi-threshold: params =', len(coeffs))
        return coeffs


    def pos_k_min(self, vals, max_params):
        return min(np.argmin(vals[:, 1]), max_params, vals.shape[0]-1)

    def mdlfit(self, xs):
        print('MDL-like estimator')
        t0 = datetime.now()
        self._fitinit(xs, cv=True)
        coeffs = self.calc_pdf_mdl(xs)
        self.pdf = self.params.calc_pdf(coeffs)
        self.name = '%s, n=%d, j0=%s, Dj=%d CV-like #params=%d' % (self.wave.name, self.params.n, str(self.jj0),
                                                              self.delta_j, len(coeffs))
        print('secs=', (datetime.now() - t0).total_seconds())

    def calc_pdf_mdl(self, xs):
        all_coeffs = []
        for key_tup in self.params.coeffs.items():
            key, tup = key_tup
            coeff, _ = tup
            if coeff == 0.0:
                continue
            coeff_contribution = self.params.calc_contribution(key, coeff, xs)
            all_coeffs.append((key, coeff_contribution))

        # sort 1 : lambda (key, Q): -Q
        all_coeffs.sort(key=lambda t: -t[1])

        # sort 2 : lambda (key, Q): (is_beta, j, -Q)
        # is_alpha = lambda key: (lambda j, qx: j == 0 and all([qi == 0 for qi in qx]))(key[0], key[1])
        # all_coeffs.sort(key=lambda t: (not is_alpha(t[0]), t[0][0], -t[1]))
        keys = []
        for key, contrib in all_coeffs: ##[:self.params.n]:
            keys.append(key)
            # if is_alpha(key): # sort 2
            #     continue
            if math.fabs(contrib) < 0.00001:
                break
        return {key:self.params.coeffs[key] for key in keys}


def coeff_sort(key_tup):
    key, tup = key_tup
    j, qx, zs, jpow2 = key
    coeff, num = tup
    is_alpha = j == 0 and all([qi == 0 for qi in qx])
    v_th = math.fabs(coeff) / math.sqrt(j + 1)
    return (not is_alpha, -v_th, key)

def coeff_sort_no_j(key_tup):
    key, tup = key_tup
    j, qx, zs, jpow2 = key
    coeff, num = tup
    is_alpha = j == 0 and all([qi == 0 for qi in qx])
    v_th = math.fabs(coeff)
    return (not is_alpha, -v_th, key)

def _cv2_key_sort(key):
    j, qx, zs, jpow2 = key
    is_alpha = j == 0 and all([qi == 0 for qi in qx])
    return (not is_alpha, -j, qx, zs)
