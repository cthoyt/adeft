import re
import gzip
import json
import logging
import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.model_selection import GridSearchCV
from sklearn.metrics import f1_score, make_scorer
from sklearn.linear_model import LogisticRegression
from sklearn.feature_extraction.text import TfidfVectorizer

from deft.util import is_jsonable


logger = logging.getLogger('classify')


class LongformClassifier(object):
    """For training classifiers to disambiguate shortforms based on context

    Fits a logistic regression with tfidf vectorized features. Uses sklearns
    LogisticRegression and TfidfVectorizer classes. Models can be serialized
    to gzipped json and loaded for later use. Capable of using a grid search to
    find optimal hyperparameter values.

    Parameters
    ----------
    shortform: str
        Shortform to disambiguate
    pos_labels: list of str
        Labels for positive classes. These are the longforms that it is
        important to ground correctly.

    Attributes
    ----------
    estimator: py:class:`sklearn.pipeline.Pipeline
        An sklearn pipeline that transforms text data with a TfidfVectorizer
        and fits a logistic regression.

    f1_score: float
       Crossvalidated f1 score of classifier on training data. The positive
       labels must be specified in constructor. For multiclass problems,
       takes the average of f1 scores for all positive labels weighted by the
       number of datapoints with each label.

    """
    def __init__(self, shortform, pos_labels):
        self.shortform = shortform
        self.pos_labels = pos_labels

    def train(self, texts, y, params=None, n_jobs=1, cv=5):
        """Performs grid search to select and fit a disambiguation model

        Parameters
        ----------
        texts: iterable of str
            Training data of texts

        y: iterable of str
            true labels for the training texts

        params: Optional[dict]
          Grid search parameters. Can include the regularization parameter C
          for logistic regression as well as max_features and ngram_range
          for the TfidfVectorizer. If not specified, defaults to setting
          performing crossvalidation with only C = 1.0, max_features = 1000,
          and ngram_range = (1, 2).

        n_jobs: Optional[int]
            Number of jobs to use when performing grid_search
            Default: 1
        cv: Optional[int]
            Number of folds to use in crossvalidation. Default: 5

        Example
        -------
        >>> params = {'C': [1.0, 10.0, 100.0],
        ...    'max_features': [3000, 6000, 9000],
        ...    'ngram_range': [(1, 1), (1, 2), (1, 3)]}
        >>> classifier = LongformClassifier('IR', ['insulin receptor'])
        >>> classifier.train(texts, labels, params=params, n_jobs=4)
        """
        # Initialize pipeline
        logit_pipeline = Pipeline([('tfidf',
                                    TfidfVectorizer(ngram_range=(1, 2),
                                                    stop_words='english')),
                                   ('logit',
                                    LogisticRegression(solver='saga',
                                                       penalty='l1',
                                                       multi_class='auto'))])

        # Default parameter values
        temp_params = {'logit__C': [1.0],
                       'tfidf__max_features': [1000]}
        # Modify default if user has specifed parameters for the grid search
        if params is not None:
            if 'C' in params:
                temp_params['logit__C'] = params['C']
            if 'max_features' in params:
                temp_params['tfidf__max_features'] = params['max_features']
            if 'ngram_range' in params:
                temp_params['tfidf__ngram_range'] = params['ngram_range']
        params = temp_params

        # Create scorer for use in grid search. Uses f1 score. The positive
        # labels are specified at the time of construction. Takes the average
        # of the f1 scores for each positive label weighted by the frequency in
        # which it appears in the training data.
        if len(set(y)) > 2:
            f1_scorer = make_scorer(f1_score, labels=self.pos_labels,
                                    average='weighted')
        else:
            f1_scorer = make_scorer(f1_score, pos_label=self.pos_labels[0],
                                    average='binary')

        logger.info('Beginning grid search in parameter space:\n'
                    f"(C={params['logit__C']})\n"
                    f"(max_features={params['tfidf__max_features']})")

        # Fit grid_search and set the estimator for the instance of the class
        grid_search = GridSearchCV(logit_pipeline, params,
                                   cv=cv, n_jobs=n_jobs, scoring=f1_scorer)
        grid_search.fit(texts, y)
        logger.info(f'Best f1 score of {grid_search.best_score_} found for'
                    f' parameter values:\n{grid_search.best_params_}')
        self.estimator = grid_search.best_estimator_
        self.best_score = grid_search.best_score_

    def predict_proba(self, texts):
        """Predict class probabilities for a list-like of texts"""
        return self.estimator.predict_proba(texts)

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
        stop_words_ = list(tfidf.stop_words_)

        params = self.estimator.get_params()
        tfidf_params = {key[7:]: value for key, value in params.items()
                        if re.match('^tfidf__', key) and is_jsonable(value)}
        logit_params = {key[7:]: value for key, value in params.items()
                        if re.match('^logit__', key) and is_jsonable(value)}

        model_info = {'logit': {'classes_': classes_,
                                'intercept_': intercept_,
                                'coef_': coef_},
                      'tfidf': {'vocabulary_': vocabulary_,
                                'idf_': idf_,
                                'stop_words_': stop_words_},
                      'tfidf_params': tfidf_params,
                      'logit_params': logit_params,
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
    longform_model: py:class:`deft.deft.classify.LongformClassifier`
    """
    with gzip.GzipFile(filepath, 'r') as fin:
        json_bytes = fin.read()
    json_str = json_bytes.decode('utf-8')
    model_info = json.loads(json_str)

    shortform = model_info['shortform']
    pos_labels = model_info['pos_labels']

    longform_model = LongformClassifier(shortform=shortform,
                                        pos_labels=pos_labels)

    tfidf_params = model_info['tfidf_params']
    tfidf_params['dtype'] = np.float64
    logit_params = model_info['logit_params']

    tfidf = TfidfVectorizer(**tfidf_params)
    logit = LogisticRegression(**logit_params)

    tfidf.vocabulary_ = model_info['tfidf']['vocabulary_']
    tfidf.idf_ = model_info['tfidf']['idf_']
    tfidf.stop_words_ = set(model_info['tfidf']['stop_words_'])

    logit.classes_ = np.array(model_info['logit']['classes_'])
    logit.intercept_ = np.array(model_info['logit']['intercept_'])
    logit.coef_ = np.array(model_info['logit']['coef_'])

    estimator = Pipeline([('tfidf', tfidf),
                          ('logit', logit)])
    longform_model.estimator = estimator
    return longform_model


