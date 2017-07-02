from collections import defaultdict

import numpy as np

from sklearn.base import clone
from sklearn.externals.joblib import Parallel, delayed, cpu_count
import sklearn.model_selection._search as sk_model_sel

from . import Optimizer
from .utils import point_asdict, dimensions_aslist
from .space import check_dimension

class BayesSearchCV(sk_model_sel.BaseSearchCV):
    """Bayesian optimization over hyper parameters.

    BayesSearchCV implements a "fit" and a "score" method.
    It also implements "predict", "predict_proba", "decision_function",
    "transform" and "inverse_transform" if they are implemented in the
    estimator used.

    The parameters of the estimator used to apply these methods are optimized
    by cross-validated search over parameter settings.

    In contrast to GridSearchCV, not all parameter values are tried out, but
    rather a fixed number of parameter settings is sampled from the specified
    distributions. The number of parameter settings that are tried is
    given by n_iter.

    Parameters are presented as a list of skopt.space.Dimension objects.

    Parameters
    ----------
    estimator : estimator object.
        A object of that type is instantiated for each search point.
        This object is assumed to implement the scikit-learn estimator api.
        Either estimator needs to provide a ``score`` function,
        or ``scoring`` must be passed.

    search_spaces : dict, list of dict or list of tuple containing
        (dict, int), or None.
        One of 4 following cases:
        1. dictionary, where keys are parameter names (strings)
        and values are skopt.space.Dimension instances (Real, Integer
        or Categorical) or any other valid value that defines skopt
        dimension (see skopt.Optimizer docs). Represents search space
        over parameters of the provided estimator.
        2. list of dictionaries: a list of dictionaries, where every
        dictionary fits the description given in case 1 above.
        If a list of dictionary objects is given, then the search is
        performed sequentially for every parameter space with maximum
        number of evaluations set to self.n_iter.
        3. list of (dict, int > 0): an extension of case 2 above,
        where first element of every tuple is a dictionary representing
        some search subspace, similarly as in case 2, and second element
        is a number of iterations that will be spent optimizing over
        this subspace.
        4. None, in which case it is assumed that a user will provide
        search space via the `add_spaces` function.

    n_iter : int, default=128
        Number of parameter settings that are sampled. n_iter trades
        off runtime vs quality of the solution.

    surrogate : string or skopt surrogate, default='auto'
        Surrogate to use for optimization of score of estimator.
        By default skopt.learning.GaussianProcessRegressor() is used.

    scoring : string, callable or None, default=None
        A string (see model evaluation documentation) or
        a scorer callable object / function with signature
        ``scorer(estimator, X, y)``.
        If ``None``, the ``score`` method of the estimator is used.

    fit_params : dict, optional
        Parameters to pass to the fit method.

    n_jobs : int, default=1
        Number of jobs to run in parallel.

    pre_dispatch : int, or string, optional
        Controls the number of jobs that get dispatched during parallel
        execution. Reducing this number can be useful to avoid an
        explosion of memory consumption when more jobs get dispatched
        than CPUs can process. This parameter can be:

            - None, in which case all the jobs are immediately
              created and spawned. Use this for lightweight and
              fast-running jobs, to avoid delays due to on-demand
              spawning of the jobs

            - An int, giving the exact number of total jobs that are
              spawned

            - A string, giving an expression as a function of n_jobs,
              as in '2*n_jobs'

    iid : boolean, default=True
        If True, the data is assumed to be identically distributed across
        the folds, and the loss minimized is the total loss per sample,
        and not the mean loss across the folds.

    cv : int, cross-validation generator or an iterable, optional
        Determines the cross-validation splitting strategy.
        Possible inputs for cv are:
          - None, to use the default 3-fold cross validation,
          - integer, to specify the number of folds in a `(Stratified)KFold`,
          - An object to be used as a cross-validation generator.
          - An iterable yielding train, test splits.

        For integer/None inputs, if the estimator is a classifier and ``y`` is
        either binary or multiclass, :class:`StratifiedKFold` is used. In all
        other cases, :class:`KFold` is used.

        Refer :ref:`User Guide <cross_validation>` for the various
        cross-validation strategies that can be used here.

    refit : boolean, default=True
        Refit the best estimator with the entire dataset.
        If "False", it is impossible to make predictions using
        this RandomizedSearchCV instance after fitting.

    verbose : integer
        Controls the verbosity: the higher, the more messages.

    random_state : int or RandomState
        Pseudo random number generator state used for random uniform sampling
        from lists of possible values instead of scipy.stats distributions.

    error_score : 'raise' (default) or numeric
        Value to assign to the score if an error occurs in estimator fitting.
        If set to 'raise', the error is raised. If a numeric value is given,
        FitFailedWarning is raised. This parameter does not affect the refit
        step, which will always raise the error.

    return_train_score : boolean, default=True
        If ``'False'``, the ``cv_results_`` attribute will not include training
        scores.

    Example
    -------

    from skopt import BayesSearchCV
    # parameter ranges are specified by one of below
    from skopt.space import Real, Categorical, Integer

    from sklearn.datasets import load_iris
    from sklearn.svm import SVC
    from sklearn.model_selection import train_test_split

    X, y = load_iris(True)
    X_train, X_test, y_train, y_test = train_test_split(X, y, train_size=0.75, random_state=0)

    # log-uniform: understand as search over p = exp(x) by varying x
    opt = BayesSearchCV(
        SVC(),
        {
            'C': Real(1e-6, 1e+6, prior='log-uniform'),
            'gamma': Real(1e-6, 1e+1, prior='log-uniform'),
            'degree': Integer(1,8),
            'kernel': Categorical(['linear', 'poly', 'rbf']),
        },
        n_iter=32
    )

    # executes bayesian optimization
    opt.fit(X_train, y_train)

    # model can be saved, used for predictions or scoring
    print(opt.score(X_test, y_test))

    Attributes
    ----------
    cv_results_ : dict of numpy (masked) ndarrays
        A dict with keys as column headers and values as columns, that can be
        imported into a pandas ``DataFrame``.

        For instance the below given table

        +--------------+-------------+-------------------+---+---------------+
        | param_kernel | param_gamma | split0_test_score |...|rank_test_score|
        +==============+=============+===================+===+===============+
        |    'rbf'     |     0.1     |        0.8        |...|       2       |
        +--------------+-------------+-------------------+---+---------------+
        |    'rbf'     |     0.2     |        0.9        |...|       1       |
        +--------------+-------------+-------------------+---+---------------+
        |    'rbf'     |     0.3     |        0.7        |...|       1       |
        +--------------+-------------+-------------------+---+---------------+

        will be represented by a ``cv_results_`` dict of::

            {
            'param_kernel' : masked_array(data = ['rbf', 'rbf', 'rbf'],
                                          mask = False),
            'param_gamma'  : masked_array(data = [0.1 0.2 0.3], mask = False),
            'split0_test_score'  : [0.8, 0.9, 0.7],
            'split1_test_score'  : [0.82, 0.5, 0.7],
            'mean_test_score'    : [0.81, 0.7, 0.7],
            'std_test_score'     : [0.02, 0.2, 0.],
            'rank_test_score'    : [3, 1, 1],
            'split0_train_score' : [0.8, 0.9, 0.7],
            'split1_train_score' : [0.82, 0.5, 0.7],
            'mean_train_score'   : [0.81, 0.7, 0.7],
            'std_train_score'    : [0.03, 0.03, 0.04],
            'mean_fit_time'      : [0.73, 0.63, 0.43, 0.49],
            'std_fit_time'       : [0.01, 0.02, 0.01, 0.01],
            'mean_score_time'    : [0.007, 0.06, 0.04, 0.04],
            'std_score_time'     : [0.001, 0.002, 0.003, 0.005],
            'params' : [{'kernel' : 'rbf', 'gamma' : 0.1}, ...],
            }

        NOTE that the key ``'params'`` is used to store a list of parameter
        settings dict for all the parameter candidates.

        The ``mean_fit_time``, ``std_fit_time``, ``mean_score_time`` and
        ``std_score_time`` are all in seconds.

    best_estimator_ : estimator
        Estimator that was chosen by the search, i.e. estimator
        which gave highest score (or smallest loss if specified)
        on the left out data. Not available if refit=False.

    best_score_ : float
        Score of best_estimator on the left out data.

    best_params_ : dict
        Parameter setting that gave the best results on the hold out data.

    best_index_ : int
        The index (of the ``cv_results_`` arrays) which corresponds to the best
        candidate parameter setting.

        The dict at ``search.cv_results_['params'][search.best_index_]`` gives
        the parameter setting for the best model, that gives the highest
        mean score (``search.best_score_``).

    scorer_ : function
        Scorer function used on the held out data to choose the best
        parameters for the model.

    n_splits_ : int
        The number of cross-validation splits (folds/iterations).

    Notes
    -----
    The parameters selected are those that maximize the score of the held-out
    data, according to the scoring parameter.

    If `n_jobs` was set to a value higher than one, the data is copied for each
    parameter setting(and not `n_jobs` times). This is done for efficiency
    reasons if individual jobs take very little time, but may raise errors if
    the dataset is large and not enough memory is available.  A workaround in
    this case is to set `pre_dispatch`. Then, the memory is copied only
    `pre_dispatch` many times. A reasonable value for `pre_dispatch` is `2 *
    n_jobs`.

    See Also
    --------
    :class:`GridSearchCV`:
        Does exhaustive search over a grid of parameters.

    """

    def __init__(self, estimator, search_spaces=None, optimizer_kwargs=None,
                 n_iter=256, scoring=None, fit_params=None, n_jobs=1,
                 iid=True, refit=True, cv=None, verbose=0,
                 pre_dispatch='2*n_jobs', random_state=None,
                 error_score='raise', return_train_score=True):

        # set of space name: space dict. Stored as a dict, in order
        # to make it easy to add or remove search spaces, in case
        # user wants to use `step` function directly.
        self.search_spaces_ = {}

        # can be None if user intends to provide spaces later manually
        if not search_spaces is None:
            # account for the case when search space is a dict
            if isinstance(search_spaces, dict):
                search_spaces = [search_spaces]
            self.add_spaces(search_spaces, range(len(search_spaces)))

        self.n_iter = n_iter
        self.random_state = random_state

        if optimizer_kwargs is None:
            self.optimizer_kwargs = {}
        else:
            self.optimizer_kwargs = optimizer_kwargs

        # this dict is used in order to keep track of skopt Optimizer
        # instances for different search spaces. `str(space)` is used as key
        # as `space` is a dict, which is unhashable. however, str(space)
        # is fixed and unique if space is not changed.
        self.optimizer_ = {}
        self.cv_results_ = defaultdict(list)

        self.best_index_ = None

        super(BayesSearchCV, self).__init__(
             estimator=estimator, scoring=scoring, fit_params=fit_params,
             n_jobs=n_jobs, iid=iid, refit=refit, cv=cv, verbose=verbose,
             pre_dispatch=pre_dispatch, error_score=error_score,
             return_train_score=return_train_score)

    def _check_search_space(self, search_space):
        """Checks whether the search space argument is correct"""

        # check if space is a single dict, convert to list if so
        if isinstance(search_space, dict):
            search_space = [search_space]

        # check if the structure of the space is proper
        if isinstance(search_space, list):
            # convert to just a list of dicts
            dicts_only = []

            # 1. check the case when a tuple of space, n_iter is provided
            for elem in search_space:
                if isinstance(elem, tuple):
                    if len(elem) != 2:
                        raise ValueError(
                            "All tuples in list of search spaces should have"
                            "length 2, and contain (dict, int), got %s" % elem
                        )
                    subspace, n_iter = elem

                    if (not isinstance(n_iter, int)) or n_iter < 0:
                        raise ValueError(
                            "Number of iterations in search space should be"
                            "positive integer, got %s in tuple %s " %
                            (n_iter, elem)
                        )

                    # save subspaces here for further checking
                    dicts_only.append(subspace)
                elif isinstance(elem, dict):
                    dicts_only.append(elem)
                else:
                    raise TypeError(
                        "A search space should be provided as a dict or"
                        "tuple (dict, int), got %s" % elem)

            # 2. check all the dicts for correctness of contents
            for subspace in dicts_only:
                for k, v in subspace.items():
                    check_dimension(v)
        else:
            raise TypeError(
                "Search space should be provided as a dict or list of dict,"
                "got %s" % search_space)

    def add_spaces(self, spaces, names):

        self._check_search_space(spaces)

        if not isinstance(spaces, list):
            spaces = [spaces]
        if not isinstance(names, list):
            names = [names]

        # first check whether space already exits ...
        for space, name in zip(spaces, names):
            if name in self.search_spaces_:
                raise ValueError("Search space %s already exists!" % name)

        for space, name in zip(spaces, names):
            self.search_spaces_[name] = space

    def _fit_best_model(self, X, y):
        """Fit the estimator copy with best parameters found to the
        provided data.

        Parameters
        ----------
        X : array-like, shape = [n_samples, n_features]
            Input data, where n_samples is the number of samples and
            n_features is the number of features.

        y : array-like, shape = [n_samples] or [n_samples, n_output],
            Target relative to X for classification or regression.

        Returns
        -------
        self
        """
        self.best_estimator_ = clone(self.estimator)
        self.best_estimator_.set_params(**self.best_params_)
        self.best_estimator_.fit(X, y)
        return self

    def _make_optimizer(self, params_space):
        """Instantiate skopt Optimizer class.

        Parameters
        ----------
        params_space : dict
            Represents parameter search space. The keys are parameter
            names (strings) and values are skopt.space.Dimension instances,
            one of Real, Integer or Categorical.

        Returns
        -------
        optimizer: Instance of the `Optimizer` class used for for search
            in some parameter space.

        """

        kwargs = self.optimizer_kwargs.copy()
        kwargs['dimensions'] = dimensions_aslist(params_space)
        optimizer = Optimizer(**kwargs)

        return optimizer

    def step(self, X, y, space_id, groups=None, n_jobs=1):
        """Generate n_jobs parameters and evaluate them in parallel.

        Having a separate function for a single step for search allows to
        save easily checkpoints for the parameter search and restore from
        possible failures.

        Parameters
        ----------
        X : array-like or sparse matrix, shape = [n_samples, n_features]
            The training input samples. Internally, it will be converted to
            ``dtype=np.float32`` and if a sparse matrix is provided
            to a sparse ``csc_matrix``.

        y : array-like, shape = [n_samples] or [n_samples, n_outputs]
            The target values (class labels) as integers or strings.

        space_id : hashable
            Identifier of parameter search space. Add search spaces with

        groups : array-like, with shape (n_samples,), optional
            Group labels for the samples used while splitting the dataset into
            train/test set.

        n_jobs : int, default=1
            Number of parameters to evaluate in parallel.

        Returns
        -------
        params_dict: dictionary with parameter values.
        """

        # convert n_jobst to int > 0 if necessary
        if n_jobs < 0:
            n_jobs = max(1, cpu_count() + n_jobs + 1)

        # use the cached optimizer for particular parameter space
        if space_id not in self.search_spaces_:
            raise ValueError("Unknown space %s" % space_id)

        # get the search space for a step
        search_space = self.search_spaces_[space_id]
        if isinstance(search_space, tuple):
            search_space, _ = search_space

        # create optimizer if not created already
        if space_id not in self.optimizer_:
            self.optimizer_[space_id] = self._make_optimizer(search_space)
        optimizer = self.optimizer_[space_id]

        # get parameter values to evaluate
        params = optimizer.ask(n_points=n_jobs)
        params_dict = [point_asdict(search_space, p) for p in params]

        # self.cv_results_ is reset at every call to _fit, keep current
        all_cv_results = self.cv_results_

        # record performances with different points
        refit = self.refit
        self.refit = False # do not fit yet - will be fit later
        self._fit(X, y, groups, params_dict)
        self.refit = refit

        # merge existing and new cv_results_
        for k in self.cv_results_:
            all_cv_results[k].extend(self.cv_results_[k])

        self.cv_results_ = all_cv_results
        self.best_index_ = np.argmax(self.cv_results_['mean_test_score'])

        # feed the point and objective back into optimizer
        local_results = self.cv_results_['mean_test_score'][-len(params):]

        # optimizer minimizes objective, hence provide negative score
        optimizer.tell(params, [-score for score in local_results])

        # fit the best model if necessary
        if self.refit:
            self._fit_best_model(X, y)

    def fit(self, X, y=None, groups=None):
        """Run fit on the estimator with randomly drawn parameters.

        Parameters
        ----------
        X : array-like, shape = [n_samples, n_features]
            Training vector, where n_samples in the number of samples and
            n_features is the number of features.

        y : array-like, shape = [n_samples] or [n_samples, n_output]
            Target relative to X for classification or regression;

        groups : array-like, with shape (n_samples,), optional
            Group labels for the samples used while splitting the dataset into
            train/test set.
        """

        # check if the list of parameter spaces is provided. If not, then
        # only step in manual mode can be used.

        if len(self.search_spaces_) == 0:
            raise ValueError(
                "Please provide search space using `add_spaces` first before"
                "calling fit method."
            )

        n_jobs = self.n_jobs

        # account for case n_jobs < 0
        if n_jobs < 0:
            n_jobs = max(1, cpu_count() + n_jobs + 1)

        for space_id in sorted(self.search_spaces_.keys()):
            elem = self.search_spaces_[space_id]

            # if not provided with search subspace, n_iter is taken as
            # self.n_iter
            if isinstance(elem, tuple):
                space, n_iter = elem
            else:
                n_iter = self.n_iter

            # do the optimization for particular search space
            while n_iter > 0:
                # when n_iter < n_jobs points left for evaluation
                n_jobs_adjusted = min(n_iter, self.n_jobs)

                self.step(
                    X, y, space_id,
                    groups=groups, n_jobs=n_jobs_adjusted
                )
                n_iter -= n_jobs
