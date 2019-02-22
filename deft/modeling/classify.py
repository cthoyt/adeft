import gzip
import json
import logging
import warnings
import numpy as np

from sklearn.pipeline import Pipeline
from sklearn.model_selection import GridSearchCV
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import f1_score, precision_score, recall_score,\
    make_scorer

warnings.filterwarnings("ignore", category=ConvergenceWarning)

logger = logging.getLogger('classify')


class DeftClassifier(object):
    """Trains classifiers to disambiguate shortforms based on context

    Fits logistic regression models with tfidf vectorized ngram features.
    Uses sklearns LogisticRegression and TfidfVectorizer classes.
    Models can be serialized and loaded for later use.

    Parameters
    ----------
    shortform: str
        Shortform to disambiguate

    pos_labels: list of str
        Labels for positive classes. These correspond to the longforms of
        interest in an application. For Deft pretrained models these are
        typically genes and other relevant biological terms.

    Attributes
    ----------
    estimator: py:class:`sklearn.pipeline.Pipeline`
        An sklearn pipeline that transforms text data with a TfidfVectorizer
        and fits a logistic regression.

    f1_score: float
       Crossvalidated f1 score of classifier on training data if fit with
       the cv method. For multiclass problems takes the average f1 score for
       all positive labels weighted by the number of datapoints with each
       label.
    """
    def __init__(self, shortform, pos_labels):
        self.shortform = shortform
        self.pos_labels = pos_labels

    def train(self, texts, y, C=1.0, ngram_range=(1, 2), max_features=1000):
        """Fits a disambiguation model

        Parameters
        ----------
        tests : iterable of str
            Training texts

        y : iterable of str
            True labels for training texts

        C : Optional[float]
             L1 regularization parameter logistic regression model. Follows
             convention of support vector machines with smaller values
             corresponding to stronger regularization. Default: 1.0

        ngram_range : Optional[tuple of int]
            Range of ngram features to use. Must be a tuple of ints of the
            form (a, b) with a <= b. When ngram_range is (1, 2), unigrams and
            bigrams will be used as features. Default: (1, 2)

        max_features : int
            Maximum number of tfidf-vectorized ngrams to use as features in
            model. Selects top_features by term frequency Default: 1000
        """
        # Initialize pipeline
        logit_pipeline = Pipeline([('tfidf',
                                    TfidfVectorizer(ngram_range=ngram_range,
                                                    max_features=max_features,
                                                    stop_words='english')),
                                   ('logit',
                                    LogisticRegression(C=C,
                                                       solver='saga',
                                                       penalty='l1',
                                                       multi_class='auto'))])

        logit_pipeline.fit(texts, y)
        self.estimator = logit_pipeline
        self.best_score = None
        self.grid_search = None

    def cv(self, texts, y, param_grid, n_jobs=1, cv=5):
        """Performs grid search to select and fit a disambiguation model

        Parameters
        ----------
        texts : iterable of str
             Training texts

        y : iterable of str
            True labels for the training texts

        param_grid : Optional[dict]
          Grid search parameters. Can contain all parameters from the train
          method.

        n_jobs : Optional[int]
            Number of jobs to use when performing grid_search
            Default: 1
        cv : Optional[int]
            Number of folds to use in crossvalidation. Default: 5

        Example
        -------
        >>> params = {'C': [1.0, 10.0, 100.0],
        ...    'max_features': [3000, 6000, 9000],
        ...    'ngram_range': [(1, 1), (1, 2), (1, 3)]}
        >>> classifier = LongformClassifier('IR', ['insulin receptor'])
        >>> classifier.train(texts, labels, param_grid=params, n_jobs=4)
        """
        # Initialize pipeline
        logit_pipeline = Pipeline([('tfidf',
                                    TfidfVectorizer(ngram_range=(1, 2),
                                                    max_features=1000,
                                                    stop_words='english')),
                                   ('logit',
                                    LogisticRegression(C=100.,
                                                       solver='saga',
                                                       penalty='l1',
                                                       multi_class='auto'))])

        # Create scorer for use in grid search. Uses f1 score. The positive
        # labels are specified at the time of construction. Takes the average
        # of the f1 scores for each positive label weighted by the frequency in
        # which it appears in the training data.
        if len(set(y)) > 2:
            f1_scorer = make_scorer(f1_score, labels=self.pos_labels,
                                    average='weighted')
            precision_scorer = make_scorer(precision_score,
                                           labels=self.pos_labels,
                                           average='weighted')
            recall_scorer = make_scorer(recall_score,
                                        labels=self.pos_labels,
                                        average='weighted')
        else:
            f1_scorer = make_scorer(f1_score, pos_label=self.pos_labels[0],
                                    average='binary')
            precision_scorer = make_scorer(precision_score,
                                           pos_label=self.pos_labels[0],
                                           average='binary')
            recall_scorer = make_scorer(recall_score,
                                        pos_label=self.pos_labels[0],
                                        average='binary')
            f1_scorer = make_scorer(f1_score, pos_label=self.pos_labels[0],
                                    average='binary')

        scorer = {'f1': f1_scorer, 'pr': precision_scorer,
                  'rc': recall_scorer}

        logger.info('Beginning grid search in parameter space:\n'
                    '%s' % param_grid)

        param_mapping = {'C': 'logit__C',
                         'max_features': 'tfidf__max_features',
                         'ngram_range':  'tfidf__ngram_range'}

        param_grid = {param_mapping[key]: value
                      for key, value in param_grid.items()}

        # Fit grid_search and set the estimator for the instance of the class
        grid_search = GridSearchCV(logit_pipeline, param_grid,
                                   cv=cv, n_jobs=n_jobs, scoring=scorer,
                                   refit='f1', iid=False)
        grid_search.fit(texts, y)
        logger.info('Best f1 score of %s found for' % grid_search.best_score_
                    + ' parameter values:\n%s' % grid_search.best_params_)

        self.estimator = grid_search.best_estimator_
        self.best_score = grid_search.best_score_
        self.grid_search = grid_search

    def predict_proba(self, texts):
        """Predict class probabilities for a list-like of texts"""
        labels = self.estimator.classes_
        preds = self.estimator.predict_proba(texts)
        return [{labels[i]: prob for i, prob in enumerate(probs)}
                for probs in preds]

    def predict(self, texts):
        """Predict class labels for a list-like of texts"""
        return self.estimator.predict(texts)

    def dump_model(self, filepath):
        """Serialize model to gzipped json

        Parameters
        ----------
        filepath: str
           Path to output file
        """
        logit = self.estimator.named_steps['logit']
        classes_ = logit.classes_.tolist()
        intercept_ = logit.intercept_.tolist()
        coef_ = logit.coef_.tolist()

        tfidf = self.estimator.named_steps['tfidf']
        vocabulary_ = {term: int(frequency)
                       for term, frequency in tfidf.vocabulary_.items()}
        idf_ = tfidf.idf_.tolist()
        ngram_range = tfidf.ngram_range
        model_info = {'logit': {'classes_': classes_,
                                'intercept_': intercept_,
                                'coef_': coef_},
                      'tfidf': {'vocabulary_': vocabulary_,
                                'idf_': idf_,
                                'ngram_range': ngram_range},
                      'shortform': self.shortform,
                      'pos_labels': self.pos_labels}
        json_str = json.dumps(model_info)
        json_bytes = json_str.encode('utf-8')
        with gzip.GzipFile(filepath, 'w') as fout:
            fout.write(json_bytes)


def load_model(filepath):
    """Load previously serialized model

    Parameters
    ----------
    filepath: str
       path to model file

    Returns
    -------
    longform_model: py:class:`deft.deft.classify.DeftClassifier`
    """
    with gzip.GzipFile(filepath, 'r') as fin:
        json_bytes = fin.read()
    json_str = json_bytes.decode('utf-8')
    model_info = json.loads(json_str)

    shortform = model_info['shortform']
    pos_labels = model_info['pos_labels']

    longform_model = DeftClassifier(shortform=shortform,
                                    pos_labels=pos_labels)
    ngram_range = model_info['tfidf']['ngram_range']
    tfidf = TfidfVectorizer(ngram_range=ngram_range)
    logit = LogisticRegression()

    tfidf.vocabulary_ = model_info['tfidf']['vocabulary_']
    tfidf.idf_ = model_info['tfidf']['idf_']

    logit.classes_ = np.array(model_info['logit']['classes_'])
    logit.intercept_ = np.array(model_info['logit']['intercept_'])
    logit.coef_ = np.array(model_info['logit']['coef_'])

    estimator = Pipeline([('tfidf', tfidf),
                          ('logit', logit)])
    longform_model.estimator = estimator
    return longform_model
