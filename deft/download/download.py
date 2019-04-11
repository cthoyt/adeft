import os
import json
import wget
import logging
import requests

from deft.locations import MODELS_PATH, S3_BUCKET_URL


logger = logging.getLogger(__file__)


def download_models(update=False, models=None):
    """Download models from S3

    Models are downloaded and placed into the models directory within deft.
    Each model contains a serialized deft classifier, a dictionary mapping
    longform texts to groundings, and a list of canonical names for each
    grounding. Within the models directory, models are stored in subdirectories
    named after the shortform they disambiguate.

    Parameters
    ---------
    update : Optional[bool]
        If True, replace all existing models with versions on S3
        otherwise only download models that aren't currently available.
        Default: True

    models : Optional[iterable of str]
        List of models to be downloaded. Allows user to select specific
        models to download. If this option is set, update will be treated
        as True regardless of how it was set. These should be considered
        as mutually exclusive parameters.
    """
    s3_models = set(get_s3_models().values())
    if models is None:
        models = s3_models
    else:
        models = set(models) & s3_models
        update = True

    downloaded_models = get_available_models()
    for model in models:
        # if update is False do not download model
        if not update and model in downloaded_models:
            continue
        # create model directory if it does not currently exist
        if not os.path.exists(os.path.join(MODELS_PATH, model)):
            os.makedirs(os.path.join(MODELS_PATH, model))
        for resource in (model + '_grounding_dict.json',
                         model + '_names.json',
                         model + '_model.gz'):
            resource_path = os.path.join(MODELS_PATH, model, resource)
            # if resource already exists, remove it since wget will not
            # overwrite existing files, choosing a new name instead
            _remove_if_exists(resource_path)
            wget.download(url=os.path.join(S3_BUCKET_URL, model, resource),
                          out=resource_path)
        if model == 'TEST':
            resource_path = os.path.join(MODELS_PATH, model,
                                         'example_training_data.json')
            _remove_if_exists(resource_path)
            wget.download(url=os.path.join(S3_BUCKET_URL, model,
                                           'example_training_data.json'),
                          out=resource_path)


def get_available_models(models_path=MODELS_PATH):
    """Returns set of all models currently in models folder"""
    output = {}
    for model in os.listdir(models_path):
        model_path = os.path.join(models_path, model)
        if os.path.isdir(model_path) and model != '__pycache__':
            if model == 'TEST':
                output['TEST'] = 'TEST'
                continue
            grounding_file = '%s_grounding_dict.json' % model
            with open(os.path.join(model_path, grounding_file), 'r') as f:
                grounding_dict = json.load(f)
            for key, value in grounding_dict.items():
                if key in output:
                    logger.warning('Shortform %s has multiple deft models'
                                   'This may lead to unexpected behavior'
                                   % key)
                else:
                    output[key] = model
    return output


def get_s3_models():
    """Returns set of all models currently available on s3"""
    result = requests.get(os.path.join(S3_BUCKET_URL, 's3_models.json'))
    try:
        output = result.json()
        assert isinstance(output, dict)
    except json.JSONDecodeError or AssertionError:
        output = {}
        logger.warning('Online deft models are currently unavailable')
    return output


def _remove_if_exists(path):
    """Remove file if it exists, otherwise do nothing

    Paramteters
    -----------
    path : str
        file to attempt to remove
    """
    try:
        os.remove(path)
    except OSError:
        pass
