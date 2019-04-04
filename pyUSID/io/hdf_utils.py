# -*- coding: utf-8 -*-
"""
Utilities for reading and writing USID data in HDF5 files

Created on Tue Nov  3 21:14:25 2015

@author: Suhas Somnath, Chris Smith
"""

from __future__ import division, print_function, absolute_import, unicode_literals
import sys
import h5py
import collections
from warnings import warn
import numpy as np
import dask.array as da
import socket
from platform import platform

from .reg_ref import simple_region_ref_copy, copy_reg_ref_reduced_dim, create_region_reference,write_region_references
from .write_utils import INDICES_DTYPE, VALUES_DTYPE, get_aux_dset_slicing, clean_string_att, make_indices_matrix, \
    Dimension, build_ind_val_matrices
from .io_utils import get_time_stamp
from .dtype_utils import contains_integers, validate_dtype
from ..__version__ import version as py_usid_version

__all__ = ['get_attr', 'get_h5_obj_refs', 'get_dimensionality', 'get_sort_order',
           'get_auxiliary_datasets', 'get_attributes', 'check_if_main', 'check_and_link_ancillary',
           'get_all_main', 'get_unit_values', 'check_for_matching_attrs', 'copy_attributes', 'reshape_to_n_dims', 'link_h5_objects_as_attrs',
           'link_h5_obj_as_alias', 'write_reduced_anc_dsets',
           'find_results_groups', 'reshape_from_n_dims', 'find_dataset', 'print_tree',
           'copy_main_attributes', 'create_empty_dataset', 'check_for_old', 'get_source_dataset',
           'link_as_main', 'write_book_keeping_attrs',
           'is_editable_h5', 'write_ind_val_dsets', 'write_reduced_spec_dsets',
           'write_simple_attrs', 'write_main_dataset', 'assign_group_index', 'create_results_group', 'create_indexed_group'
           ]

if sys.version_info.major == 3:
    unicode = str

# TODO: Next version should account for two objects being in different files!


def print_tree(parent, rel_paths=False, main_dsets_only=False):
    """
    Simple function to recursively print the contents of an hdf5 group

    Parameters
    ----------
    parent : :class:`h5py.Group`
        HDF5 (sub-)tree to print
    rel_paths : bool, optional. Default = False
        True - prints the relative paths for all elements.
        False - prints a tree-like structure with only the element names
    main_dsets_only : bool, optional. default=False
        True - prints only groups and Main datasets
        False - prints all dataset and group objects
    """

    def __print(name, obj):
        show = True
        if main_dsets_only:
            show = False
            if check_if_main(obj) or isinstance(obj, h5py.Group):
                show = True
        if not show:
            return

        if rel_paths:
            print(name)
        else:
            levels = name.count('/')
            curr_name = name[name.rfind('/') + 1:]

            print(levels * '  ' + '├ ' + curr_name)
            if isinstance(obj, h5py.Group):
                print((levels + 1) * '  ' + len(curr_name) * '-')

    print(parent.name)
    parent.visititems(__print)


def get_all_main(parent, verbose=False):
    """
    Simple function to recursively print the contents of an hdf5 group

    Parameters
    ----------
    parent : :class:`h5py.Group`
        HDF5 Group to search within
    verbose : bool, optional. Default = False
        If true, extra print statements (usually for debugging) are enabled

    Returns
    -------
    main_list : list of h5py.Dataset
        The datasets found in the file that meet the 'Main Data' criteria.

    """
    if not isinstance(parent, (h5py.Group, h5py.File)):
        raise TypeError('parent should be a h5py.File or h5py.Group object')

    from .usi_data import USIDataset

    main_list = list()

    def __check(name, obj):
        if verbose:
            print(name, obj)
        if isinstance(obj, h5py.Dataset):
            if verbose:
                print(name, 'is an HDF5 Dataset.')
            ismain = check_if_main(obj)
            if ismain:
                if verbose:
                    print(name, 'is a `Main` dataset.')
                main_list.append(USIDataset(obj))

    if verbose:
        print('Checking the group {} for `Main` datasets.'.format(parent.name))
    parent.visititems(__check)

    return main_list


def get_auxiliary_datasets(h5_object, aux_dset_name=None):
    """
    Returns auxiliary dataset objects associated with some DataSet through its attributes.
    Note - region references will be ignored.

    Parameters
    ----------
    h5_object : :class:`h5py.Dataset`, :class:`h5py.Group` or :class:`h5py.File`
        Dataset object reference.
    aux_dset_name : str or :class:`list` of str, optional. Default = all
        Name of auxiliary :class:`h5py.Dataset` objects to return.

    Returns
    -------
    list of :class:`h5py.Reference` of auxiliary :class:`h5py.Dataset` objects.

    """
    if not isinstance(h5_object, (h5py.Dataset, h5py.Group, h5py.File)):
        raise TypeError('h5_object should be a h5py.Dataset, h5py.Group or h5py.File object')

    if aux_dset_name is None:
        aux_dset_name = h5_object.attrs.keys()
    else:
        if isinstance(aux_dset_name, (str, unicode)):
            aux_dset_name = [aux_dset_name]
        if not isinstance(aux_dset_name, (list, tuple)):
            raise TypeError('aux_dset_name should be a string or list / tuple of strings')
        if not np.all([isinstance(x, (str, unicode)) for x in aux_dset_name]):
            raise TypeError('aux_dset_name should be a string or list / tuple of strings')

    data_list = list()
    curr_name = None
    try:
        h5_file = h5_object.file
        for curr_name in aux_dset_name:
            h5_ref = h5_object.attrs[curr_name]
            if isinstance(h5_ref, h5py.Reference) and isinstance(h5_file[h5_ref], h5py.Dataset) and not \
                    isinstance(h5_ref, h5py.RegionReference):
                data_list.append(h5_file[h5_ref])
    except KeyError:
        raise KeyError('%s is not an attribute of %s' % (str(curr_name), h5_object.name))

    return data_list


def get_attr(h5_object, attr_name):
    """
    Returns the attribute from the h5py object

    Parameters
    ----------
    h5_object : :class:`h5py.Dataset`, :class:`h5py.Group` or :class:`h5py.File`
        object whose attribute is desired
    attr_name : str
        Name of the attribute of interest

    Returns
    -------
    att_val : object
        value of attribute, in certain cases (byte strings or list of byte strings) reformatted to readily usable forms

    """
    if not isinstance(h5_object, (h5py.Dataset, h5py.Group, h5py.File)):
        raise TypeError('h5_object should be a h5py.Dataset, h5py.Group or h5py.File object')

    if not isinstance(attr_name, (str, unicode)):
        raise TypeError('attr_name should be a string')

    if attr_name not in h5_object.attrs.keys():
        raise KeyError("'{}' is not an attribute in '{}'".format(attr_name, h5_object.name))

    att_val = h5_object.attrs.get(attr_name)
    if isinstance(att_val, np.bytes_) or isinstance(att_val, bytes):
        att_val = att_val.decode('utf-8')

    elif type(att_val) == np.ndarray:
        if sys.version_info.major == 3:
            if att_val.dtype.type in [np.bytes_, np.object_]:
                att_val = np.array([str(x, 'utf-8') for x in att_val])

    return att_val


def get_attributes(h5_object, attr_names=None):
    """
    Returns attribute associated with some DataSet.

    Parameters
    ----------
    h5_object : :class:`h5py.Dataset`
        Dataset object reference.
    attr_names : str or :class:`list` of str, optional. Default = all
        Name of attribute object to return.

    Returns
    -------
    att_dict : dict
        Dictionary containing (name,value) pairs of attributes

    """
    if not isinstance(h5_object, (h5py.Dataset, h5py.Group, h5py.File)):
        raise TypeError('h5_object should be a h5py.Dataset, h5py.Group or h5py.File object')

    if attr_names is None:
        attr_names = h5_object.attrs.keys()
    else:
        if isinstance(attr_names, (str, unicode)):
            attr_names = [attr_names]
        if not isinstance(attr_names, (list, tuple)):
            raise TypeError('attr_names should be a string or list / tuple of strings')
        if not np.all([isinstance(x, (str, unicode)) for x in attr_names]):
            raise TypeError('attr_names should be a string or list / tuple of strings')

    att_dict = {}

    for attr in attr_names:
        try:
            att_dict[attr] = get_attr(h5_object, attr)
        except KeyError:
            raise KeyError('%s is not an attribute of %s' % (str(attr), h5_object.name))

    return att_dict


def get_h5_obj_refs(obj_names, h5_refs):
    """
    Given a list of H5 references and a list of names,
    this method returns H5 objects corresponding to the names

    Parameters
    ----------
    obj_names : string or List of strings
        names of target h5py objects
    h5_refs : H5 object reference or List of H5 object references
        list containing the target reference

    Returns
    -------
    found_objects : List of HDF5 dataset references
        Corresponding references

    """
    from .usi_data import USIDataset

    if isinstance(obj_names, (str, unicode)):
        obj_names = [obj_names]
    if not isinstance(obj_names, (list, tuple)):
        raise TypeError('obj_names should be a string or list of strings')
    if not np.all([isinstance(x, (str, unicode)) for x in obj_names]):
        raise TypeError('obj_names should be a string or list / tuple of strings')

    if isinstance(h5_refs, (h5py.File, h5py.Group, h5py.Dataset)):
        h5_refs = [h5_refs]
    if not isinstance(h5_refs, (list, tuple)):
        raise TypeError('h5_refs should be a / list of h5py.Dataset, h5py.Group or h5py.File object(s)')

    found_objects = []
    for target_name in obj_names:
        for h5_object in h5_refs:
            if not isinstance(h5_object, (h5py.File, h5py.Group, h5py.Dataset)):
                continue
            if h5_object.name.split('/')[-1] == target_name:
                try:
                    found_objects.append(USIDataset(h5_object))
                except TypeError:
                    found_objects.append(h5_object)

    return found_objects


def find_dataset(h5_group, dset_name):
    """
    Uses visit() to find all datasets with the desired name

    Parameters
    ----------
    h5_group : :class:`h5py.Group`
        Group to search within for the Dataset
    dset_name : str
        Name of the dataset to search for

    Returns
    -------
    datasets : list
        List of [Name, object] pairs corresponding to datasets that match `ds_name`.

    """
    from .usi_data import USIDataset

    if not isinstance(h5_group, (h5py.File, h5py.Group)):
        raise TypeError('h5_group should be a h5py.File or h5py.Group object')
    if not isinstance(dset_name, (str, unicode)):
        raise TypeError('dset_name should be a string')

    # print 'Finding all instances of', ds_name
    datasets = []

    def __find_name(name, obj):
        if dset_name in name.split('/')[-1] and isinstance(obj, h5py.Dataset):
            try:
                datasets.append(USIDataset(obj))
            except TypeError:
                datasets.append(obj)
        return

    h5_group.visititems(__find_name)

    return datasets


def find_results_groups(h5_main, tool_name):
    """
    Finds a list of all groups containing results of the process of name `tool_name` being applied to the dataset

    Parameters
    ----------
    h5_main : h5 dataset reference
        Reference to the target dataset to which the tool was applied
    tool_name : String / unicode
        Name of the tool applied to the target dataset

    Returns
    -------
    groups : list of references to :class:`h5py.Group` objects
        groups whose name contains the tool name and the dataset name

    """
    if not isinstance(h5_main, h5py.Dataset):
        raise TypeError('h5_main should be a h5py.Dataset object')
    if not isinstance(tool_name, (str, unicode)):
        raise TypeError('tool_name should be a string')

    dset_name = h5_main.name.split('/')[-1]
    h5_parent_group = h5_main.parent
    groups = []
    for key in h5_parent_group.keys():
        if dset_name in key and tool_name in key and isinstance(h5_parent_group[key], h5py.Group):
            groups.append(h5_parent_group[key])
    return groups


def check_and_link_ancillary(h5_dset, anc_names, h5_main=None, anc_refs=None):
    """
    This function will add references to auxilliary datasets as attributes
    of an input dataset.
    If the entries in anc_refs are valid references, they will be added
    as attributes with the name taken from the corresponding entry in
    anc_names.
    If an entry in anc_refs is not a valid reference, the function will
    attempt to get the attribute with the same name from the h5_main
    dataset

    Parameters
    ----------
    h5_dset : HDF5 Dataset
        dataset to which the attributes will be written
    anc_names : list of str
        the attribute names to be used
    h5_main : HDF5 Dataset, optional
        dataset from which attributes will be copied if `anc_refs` is None
    anc_refs : list of HDF5 Object References, optional
        references that correspond to the strings in `anc_names`

    Returns
    -------
    None

    Notes
    -----
    Either `h5_main` or `anc_refs` MUST be provided and `anc_refs` has the
    higher priority if both are present.

    """
    if not isinstance(h5_dset, h5py.Dataset):
        raise TypeError('h5_dset should be a h5py.Dataset object')
    if not isinstance(anc_names, (list, tuple)):
        raise TypeError('anc_names should be a list / tuple')
    if h5_main is not None:
        if not isinstance(h5_main, h5py.Dataset):
            raise TypeError('h5_main should be a h5py.Dataset object')
    if anc_refs is not None:
        if not isinstance(anc_refs, (list, tuple)):
            raise TypeError('anc_refs should be a list / tuple')

    def __check_and_link_single(h5_obj_ref, target_ref_name):
        if isinstance(h5_obj_ref, h5py.Reference):
            h5_dset.attrs[target_ref_name] = h5_obj_ref
        elif isinstance(h5_obj_ref, h5py.Dataset):
            h5_dset.attrs[target_ref_name] = h5_obj_ref.ref
        elif h5_main is not None:
            h5_anc = get_auxiliary_datasets(h5_main, aux_dset_name=[target_ref_name])
            if len(h5_anc) == 1:
                link_h5_obj_as_alias(h5_dset, h5_anc[0], target_ref_name)
        else:
            warnstring = '{} is not a valid h5py Reference and will be skipped.'.format(repr(h5_obj_ref))
            warn(warnstring)

    if bool(np.iterable(anc_refs) and not isinstance(anc_refs, h5py.Dataset)):
        """
        anc_refs can be iterated over
        """
        for ref_name, h5_ref in zip(anc_names, anc_refs):
            __check_and_link_single(h5_ref, ref_name)
    elif anc_refs is not None:
        """
        anc_refs is just a single value
        """
        __check_and_link_single(anc_refs, anc_names)
    elif isinstance(anc_names, str) or isinstance(anc_names, unicode):
        """
        Single name provided
        """
        __check_and_link_single(None, anc_names)
    else:
        """
        Iterable of names provided
        """
        for name in anc_names:
            __check_and_link_single(None, name)

    h5_dset.file.flush()


def reshape_to_n_dims(h5_main, h5_pos=None, h5_spec=None, get_labels=False, verbose=False, sort_dims=False,
                      as_dask_array=False):
    """
    Reshape the input 2D matrix to be N-dimensions based on the
    position and spectroscopic datasets.

    Parameters
    ----------
    h5_main : HDF5 Dataset
        2D data to be reshaped
    h5_pos : HDF5 Dataset, optional
        Position indices corresponding to rows in `h5_main`
    h5_spec : HDF5 Dataset, optional
        Spectroscopic indices corresponding to columns in `h5_main`
    get_labels : bool, optional
        Whether or not to return the dimension labels.  Default False
    verbose : bool, optional
        Whether or not to print debugging statements
    sort_dims : bool
        If True, the data is sorted so that the dimensions are in order from fastest to slowest
        If False, the data is kept in the original order
        If `get_labels` is also True, the labels are sorted as well.
    as_dask_array : bool, optional. Default = False
        If False, ds_Nd will be a numpy.ndarray object - this is suitable if the HDF5 dataset fits into memory
        If True, ds_Nd will be a dask.array object - This is suitable if the HDF5 dataset is too large to fit into
        memory. Note that this will bea lazy computation meaning that the returned object just contains the instructions
        . In order to get the actual value or content in numpy arrays, call ds_Nd.compute()

    Returns
    -------
    ds_Nd : N-D numpy array or dask.array object
        N dimensional array arranged as [positions slowest to fastest, spectroscopic slowest to fastest]
    success : boolean or string
        True if full reshape was successful

        "Positions" if it was only possible to reshape by
        the position dimensions

        False if no reshape was possible
    ds_labels : list of str
        List of the labels of each dimension of `ds_Nd`

    Notes
    -----
    If either `h5_pos` or `h5_spec` are not provided, the function will first
    attempt to find them as attributes of `h5_main`.  If that fails, it will
    generate dummy values for them.

    """
    # TODO: automatically switch on as_dask_array if the data is larger than memory
    # TODO: sort_dims does not appear to do much. Functions as though it was always True

    if h5_pos is None and h5_spec is None:
        if not check_if_main(h5_main):
            raise ValueError('if h5_main is a h5py.Dataset it should be a Main dataset')
    else:
        if not isinstance(h5_main, (h5py.Dataset, np.ndarray)):
            raise TypeError('h5_main should either be a h5py.Dataset or numpy array')

    if h5_pos is not None:
        if not isinstance(h5_pos, (h5py.Dataset, np.ndarray)):
            raise TypeError('h5_pos should either be a h5py.Dataset or numpy array')
        if h5_pos.shape[0] != h5_main.shape[0]:
            raise ValueError('The size of h5_pos: {} does not match with h5_main: {}'.format(h5_pos.shape,
                                                                                             h5_main.shape))

    if h5_spec is not None:
        if not isinstance(h5_spec, (h5py.Dataset, np.ndarray)):
            raise TypeError('h5_spec should either be a h5py.Dataset or numpy array')
        if h5_spec.shape[1] != h5_main.shape[1]:
            raise ValueError('The size of h5_spec: {} does not match with h5_main: {}'.format(h5_spec.shape,
                                                                                              h5_main.shape))

    pos_labs = np.array(['Positions'])
    spec_labs = np.array(['Spectral_Step'])
    if h5_pos is None:
        """
        Get the Position datasets from the references if possible
        """
        if isinstance(h5_main, h5py.Dataset):
            try:
                h5_pos = h5_main.file[h5_main.attrs['Position_Indices']]
                ds_pos = h5_pos[()]
                pos_labs = get_attr(h5_pos, 'labels')
            except KeyError:
                print('No position datasets found as attributes of {}'.format(h5_main.name))
                if len(h5_main.shape) > 1:
                    ds_pos = np.arange(h5_main.shape[0], dtype=INDICES_DTYPE).reshape(-1, 1)
                    pos_labs = np.array(['Position Dimension {}'.format(ipos) for ipos in range(ds_pos.shape[1])])
                else:
                    ds_pos = np.array(0, dtype=INDICES_DTYPE).reshape(-1, 1)
        else:
            ds_pos = np.arange(h5_main.shape[0], dtype=INDICES_DTYPE).reshape(-1, 1)
            pos_labs = np.array(['Position Dimension {}'.format(ipos) for ipos in range(ds_pos.shape[1])])
    elif isinstance(h5_pos, h5py.Dataset):
        """
    Position Indices dataset was provided
        """
        ds_pos = h5_pos[()]
        pos_labs = get_attr(h5_pos, 'labels')
    elif isinstance(h5_pos, np.ndarray):
        ds_pos = np.atleast_2d(h5_pos)
        pos_labs = np.array(['Position Dimension {}'.format(ipos) for ipos in range(ds_pos.shape[1])])
    else:
        raise TypeError('Position Indices must be either h5py.Dataset or None')

    if h5_spec is None:
        """
        Get the Spectroscopic datasets from the references if possible
        """
        if isinstance(h5_main, h5py.Dataset):
            try:
                h5_spec = h5_main.file[h5_main.attrs['Spectroscopic_Indices']]
                ds_spec = h5_spec[()]
                spec_labs = get_attr(h5_spec, 'labels')
            except KeyError:
                print('No spectroscopic datasets found as attributes of {}'.format(h5_main.name))
                if len(h5_main.shape) > 1:
                    ds_spec = np.arange(h5_main.shape[1], dtype=INDICES_DTYPE).reshape([1, -1])
                    spec_labs = np.array(['Spectral Dimension {}'.format(ispec) for ispec in range(ds_spec.shape[0])])
                else:
                    ds_spec = np.array(0, dtype=INDICES_DTYPE).reshape([1, 1])
        else:
            ds_spec = np.arange(h5_main.shape[1], dtype=INDICES_DTYPE).reshape([1, -1])
            spec_labs = np.array(['Spectral Dimension {}'.format(ispec) for ispec in range(ds_spec.shape[0])])

    elif isinstance(h5_spec, h5py.Dataset):
        """
    Spectroscopic Indices dataset was provided
        """
        ds_spec = h5_spec[()]
        spec_labs = get_attr(h5_spec, 'labels')
    elif isinstance(h5_spec, np.ndarray):
        ds_spec = h5_spec
        spec_labs = np.array(['Spectral Dimension {}'.format(ispec) for ispec in range(ds_spec.shape[0])])
    else:
        raise TypeError('Spectroscopic Indices must be either h5py.Dataset or None')

    '''
    Sort the indices from fastest to slowest
    '''
    pos_sort = get_sort_order(np.transpose(ds_pos))
    spec_sort = get_sort_order(ds_spec)

    if verbose:
        print('Position dimensions:', pos_labs)
        print('Position sort order:', pos_sort)
        print('Spectroscopic Dimensions:', spec_labs)
        print('Spectroscopic sort order:', spec_sort)

    '''
    Get the size of each dimension in the sorted order
    '''
    pos_dims = get_dimensionality(np.transpose(ds_pos), pos_sort)
    spec_dims = get_dimensionality(ds_spec, spec_sort)

    if verbose:
        print('\nPosition dimensions (sort applied):', pos_labs[pos_sort])
        print('Position dimensionality (sort applied):', pos_dims)
        print('Spectroscopic dimensions (sort applied):', spec_labs[spec_sort])
        print('Spectroscopic dimensionality (sort applied):', spec_dims)

    if as_dask_array:
        ds_main = da.from_array(h5_main, chunks=h5_main.chunks)
    else:
        ds_main = h5_main[()]

    """
    Now we reshape the dataset based on those dimensions
    numpy reshapes correctly when the dimensions are arranged from slowest to fastest. 
    Since the sort orders we have are from fastest to slowest, we need to reverse the orders
    for both the position and spectroscopic dimensions
    """
    try:
        ds_Nd = ds_main.reshape(pos_dims[::-1] + spec_dims[::-1])

    except ValueError:
        warn('Could not reshape dataset to full N-dimensional form.  Attempting reshape based on position only.')
        try:
            ds_Nd = ds_main.reshape(pos_dims[::-1] + [-1])

        except ValueError:
            warn('Reshape by position only also failed.  Will keep dataset in 2d form.')
            if get_labels:
                return ds_main, False, ['Position', 'Spectral Step']
            else:
                return ds_main, False

        # No exception
        else:
            if get_labels:
                return ds_Nd, 'Positions', ['Position'] + spec_labs
            else:
                return ds_Nd, 'Positions'

    all_labels = np.hstack((pos_labs[pos_sort][::-1],
                            spec_labs[spec_sort][::-1]))

    if verbose:
        print('\nAfter first reshape, labels are', all_labels)
        print('Data shape is', ds_Nd.shape)

    """
    Now we transpose the axes for both the position and spectroscopic dimensions
    so that they are in the same order as in the index array
    """

    swap_axes = list()
    if sort_dims:
        for lab in pos_labs[pos_sort]:
            swap_axes.append(np.argwhere(all_labels == lab).squeeze())
        for lab in spec_labs[spec_sort]:
            swap_axes.append(np.argwhere(all_labels == lab).squeeze())
    else:
        for lab in pos_labs:
            swap_axes.append(np.argwhere(all_labels == lab).squeeze())
        for lab in spec_labs:
            swap_axes.append(np.argwhere(all_labels == lab).squeeze())

    swap_axes = np.array(swap_axes)

    if verbose:
        print('\nAxes will permuted in this order:', swap_axes)
        print('New labels ordering:', all_labels[swap_axes])

    ds_Nd = ds_Nd.transpose(tuple(swap_axes))

    results = [ds_Nd, True]

    if verbose:
        print('Dataset now of shape:', ds_Nd.shape)

    if get_labels:
        '''
        Get the labels in the proper order
        '''
        results.append(all_labels[swap_axes])

    return results


def reshape_from_n_dims(data_n_dim, h5_pos=None, h5_spec=None, verbose=False):
    """
    Reshape the input 2D matrix to be N-dimensions based on the
    position and spectroscopic datasets.

    Parameters
    ----------
    data_n_dim : numpy.array or dask.array.core.Array
        N dimensional array arranged as [positions dimensions..., spectroscopic dimensions]
        If h5_pos and h5_spec are not provided, this function will have to assume that the dimensions
        are arranged as [positions slowest to fastest, spectroscopic slowest to fastest].
        This restriction is removed if h5_pos and h5_spec are provided
    h5_pos : HDF5 Dataset, numpy.array or dask.array.core.Array
        Position indices corresponding to rows in the final 2d array
        The dimensions should be arranged in terms of rate of change corresponding to data_n_dim.
        In other words if data_n_dim had two position dimensions arranged as [pos_fast, pos_slow, spec_dim_1....],
        h5_pos should be arranged as [pos_fast, pos_slow]
    h5_spec : HDF5 Dataset, numpy. array or dask.array.core.Array
        Spectroscopic indices corresponding to columns in the final 2d array
        The dimensions should be arranged in terms of rate of change corresponding to data_n_dim.
        In other words if data_n_dim had two spectral dimensions arranged as [pos_dim_1,..., spec_fast, spec_slow],
        h5_spec should be arranged as [pos_slow, pos_fast]
    verbose : bool, optional. Default = False
        Whether or not to print log statements

    Returns
    -------
    ds_2d : numpy.array
        2 dimensional numpy array arranged as [positions, spectroscopic]
    success : boolean or string
        True if full reshape was successful

        "Positions" if it was only possible to reshape by
        the position dimensions

        False if no reshape was possible

    Notes
    -----
    If either `h5_pos` or `h5_spec` are not provided, the function will
    assume the first dimension is position and the remaining are spectroscopic already
    in order from fastest to slowest.

    """
    if not isinstance(data_n_dim, (np.ndarray, da.core.Array)):
        raise TypeError('data_n_dim is not a numpy or dask array')

    if h5_spec is None and h5_pos is None:
        raise ValueError('at least one of h5_pos or h5_spec must be specified for an attempt to reshape to 2D')

    if data_n_dim.ndim < 2:
        return data_n_dim, True

    if h5_pos is None:
        pass
    elif isinstance(h5_pos, h5py.Dataset):
        '''
        Position Indices dataset was provided
        '''
        ds_pos = h5_pos[()]
    elif isinstance(h5_pos, da.core.Array):
        ds_pos = h5_pos.compute()

    elif isinstance(h5_pos, np.ndarray):
        ds_pos = h5_pos
    else:
        raise TypeError('Position Indices must be either h5py.Dataset or None')

    if h5_spec is None:
        pass
    elif isinstance(h5_spec, h5py.Dataset):
        '''
        Spectroscopic Indices dataset was provided
        '''
        ds_spec = h5_spec[()]
    elif isinstance(h5_spec, da.core.Array):
        ds_spec = h5_spec.compute()
    elif isinstance(h5_spec, np.ndarray):
        ds_spec = h5_spec
    else:
        raise TypeError('Spectroscopic Indices must be either h5py.Dataset or None')

    if h5_spec is None and h5_pos is not None:
        if verbose:
            print('Spectral indices not provided but position indices provided.\n'
                  'Building spectral indices assuming that dimensions are arranged as slow -> fast')
        pos_dims = get_dimensionality(ds_pos, index_sort=get_sort_order(ds_pos))
        if not np.all([x in data_n_dim.shape for x in pos_dims]):
            raise ValueError('Dimension sizes in pos_dims: {} do not exist in data_n_dim shape: '
                             '{}'.format(pos_dims, data_n_dim.shape))
        spec_dims = [col for col in list(data_n_dim.shape[len(pos_dims):])]
        if verbose:
            print('data has dimensions: {}. Provided position indices had dimensions of size: {}. Spectral dimensions '
                  'will built with dimensions: {}'.format(data_n_dim.shape, pos_dims, spec_dims))
        ds_spec = make_indices_matrix(spec_dims, is_position=False)

    elif h5_pos is None and h5_spec is not None:
        if verbose:
            print('Position indices not provided but spectral indices provided.\n'
                  'Building position indices assuming that dimensions are arranged as slow -> fast')
        spec_dims = get_dimensionality(ds_spec, index_sort=get_sort_order(ds_spec))
        if not np.all([x in data_n_dim.shape for x in spec_dims]):
            raise ValueError('Dimension sizes in spec_dims: {} do not exist in data_n_dim shape: '
                             '{}'.format(spec_dims, data_n_dim.shape))
        pos_dims = [col for col in list(data_n_dim.shape[:data_n_dim.ndim-len(spec_dims)])]
        if verbose:
            print('data has dimensions: {}. Spectroscopic position indices had dimensions of size: {}. Position '
                  'dimensions will built with dimensions: {}'.format(data_n_dim.shape, spec_dims, pos_dims))
        ds_pos = make_indices_matrix(pos_dims, is_position=True)

    elif h5_spec is not None and h5_pos is not None:
        if ds_pos.shape[0] * ds_spec.shape[1] != np.product(data_n_dim.shape):
            raise ValueError('The product ({}) of the number of positions ({}) and spectroscopic ({}) observations is '
                             'not equal to the product ({}) of the data shape ({})'
                             '.'.format(ds_pos.shape[0] * ds_spec.shape[1], ds_pos.shape[0], ds_spec.shape[1],
                                        np.product(data_n_dim.shape), data_n_dim.shape))

        if ds_pos.shape[1] + ds_spec.shape[0] != data_n_dim.ndim:
            # This may mean that the dummy position or spectroscopic axes has been squeezed out!
            # Dask does NOT allow singular dimensions apparently. So cannot do expand_dims. Handle later

            if ds_pos.size == 1 or ds_spec.size == 1:
                if verbose:
                    print('ALL Position dimensions squeezed: {}. ALL Spectroscopic dimensions squeezed: {}'
                          '.'.format(ds_pos.size == 1, ds_spec.size == 1))
            else:
                raise ValueError('The number of position ({}) and spectroscopic ({}) dimensions do not match with the '
                                 'dimensionality of the N-dimensional dataset: {}'
                                 '.'.format(ds_pos.shape[1], ds_spec.shape[0], data_n_dim.ndim))

    '''
    Sort the indices from fastest to slowest
    '''
    if ds_pos.size == 1:
        # Position dimension squeezed out:
        pos_sort = []
    else:
        pos_sort = get_sort_order(np.transpose(ds_pos))

    if ds_spec.size == 1:
        # Spectroscopic axis squeezed out:
        spec_sort = []
    else:
        spec_sort = get_sort_order(ds_spec)

    if h5_spec is None:
        spec_sort = spec_sort[::-1]
    if h5_pos is None:
        pos_sort = pos_sort[::-1]

    if verbose:
        print('Position sort order: {}'.format(pos_sort))
        print('Spectroscopic sort order: {}'.format(spec_sort))

    '''
    Now we transpose the axes associated with the spectroscopic dimensions
    so that they are in the same order as in the index array
    '''
    swap_axes = np.uint16(np.append(pos_sort[::-1], spec_sort[::-1] + len(pos_sort)))

    if verbose:
        print('swap axes: {} to be applied to N dimensional data of shape {}'.format(swap_axes, data_n_dim.shape))

    data_n_dim_2 = data_n_dim.transpose(tuple(swap_axes))

    if verbose:
        print('N dimensional data shape after axes swap: {}'.format(data_n_dim_2.shape))

    '''
    Now we reshape the dataset based on those dimensions
    We must use the spectroscopic dimensions in reverse order
    '''
    try:
        ds_2d = data_n_dim_2.reshape([ds_pos.shape[0], ds_spec.shape[1]])
    except ValueError:
        raise ValueError('Could not reshape dataset to full N-dimensional form')

    return ds_2d, True


def get_dimensionality(ds_index, index_sort=None):
    """
    Get the size of each index dimension in a specified sort order

    Parameters
    ----------
    ds_index : 2D HDF5 Dataset or numpy array
        Row matrix of indices
    index_sort : Iterable of unsigned integers (Optional)
        Sort that can be applied to dimensionality.
        For example - Order of rows sorted from fastest to slowest

    Returns
    -------
    sorted_dims : list of unsigned integers
        Dimensionality of each row in ds_index.  If index_sort is supplied, it will be in the sorted order

    """
    if not isinstance(ds_index, (np.ndarray, h5py.Dataset)):
        raise TypeError('ds_index should either be a numpy array or h5py.Dataset')

    if ds_index.shape[0] > ds_index.shape[1]:
        # must be spectroscopic like in shape (few rows, more cols)
        ds_index = np.transpose(ds_index)

    if index_sort is None:
        index_sort = np.arange(ds_index.shape[0])
    else:
        if not contains_integers(index_sort, min_val=0):
            raise ValueError('index_sort should contain integers > 0')
        index_sort = np.array(index_sort)
        if index_sort.ndim != 1:
            raise ValueError('index_sort should be a 1D array')
        if len(np.unique(index_sort)) > ds_index.shape[0]:
            raise ValueError('length of index_sort ({}) should be smaller than number of dimensions in provided dataset'
                             ' ({}'.format(len(np.unique(index_sort)), ds_index.shape[0]))
        if set(np.arange(ds_index.shape[0])) !=  set(index_sort):
            raise ValueError('Sort order of dimensions ({}) not  matching  with number of dimensions ({})'
                             ''.format(index_sort, ds_index.shape[0]))

    sorted_dims = [len(np.unique(row)) for row in np.array(ds_index, ndmin=2)[index_sort]]
    return sorted_dims


def get_sort_order(ds_spec):
    """
    Find how quickly the spectroscopic values are changing in each row
    and the order of rows from fastest changing to slowest.

    Parameters
    ----------
    ds_spec : 2D HDF5 dataset or numpy array
        Rows of indices to be sorted from fastest changing to slowest

    Returns
    -------
    change_sort : List of unsigned integers
        Order of rows sorted from fastest changing to slowest

    """
    if not isinstance(ds_spec, (np.ndarray, h5py.Dataset)):
        raise TypeError('ds_spec should either be a numpy array or h5py.Dataset')

    if ds_spec.shape[0] > ds_spec.shape[1]:
        # must be spectroscopic like in shape (few rows, more cols)
        ds_spec = np.transpose(ds_spec)

    change_count = [len(np.where([row[i] != row[i - 1] for i in range(len(row))])[0]) for row in ds_spec]
    change_sort = np.argsort(change_count)[::-1]

    return change_sort


def create_empty_dataset(source_dset, dtype, dset_name, h5_group=None, new_attrs=None, skip_refs=False):
    """
    Creates an empty dataset in the h5 file based on the provided dataset in the same or specified group

    Parameters
    ----------
    source_dset : h5py.Dataset object
        Source object that provides information on the group and shape of the dataset
    dtype : dtype
        Data type of the fit / guess datasets
    dset_name : String / Unicode
        Name of the dataset
    h5_group : :class:`h5py.Group`, optional. Default = None
        Group within which this dataset will be created
    new_attrs : dictionary (Optional)
        Any new attributes that need to be written to the dataset
    skip_refs : boolean, optional
        Should ObjectReferences and RegionReferences be skipped when copying attributes from the
        `source_dset`

    Returns
    -------
    h5_new_dset : h5py.Dataset object
        Newly created dataset

    """
    if not isinstance(source_dset, h5py.Dataset):
        raise TypeError('source_deset should be a h5py.Dataset object')
    _ = validate_dtype(dtype)
    if new_attrs is not None:
        if not isinstance(new_attrs, dict):
            raise TypeError('new_attrs should be a dictionary')
    else:
        new_attrs = dict()

    if h5_group is None:
        h5_group = source_dset.parent
    else:
        if not isinstance(h5_group, (h5py.Group, h5py.File)):
            raise TypeError('h5_group should be a h5py.Group or h5py.File object')

    if not isinstance(dset_name, (str, unicode)):
        raise TypeError('dset_name should be a string')
    dset_name = dset_name.strip()
    if len(dset_name) == 0:
        raise ValueError('dset_name cannot be empty!')
    if '-' in dset_name:
        warn('dset_name should not contain the "-" character. Reformatted name from:{} to '
             '{}'.format(dset_name, dset_name.replace('-', '_')))
    dset_name = dset_name.replace('-', '_')

    kwargs = {'shape': source_dset.shape, 'dtype': dtype, 'compression': source_dset.compression,
              'chunks': source_dset.chunks}

    if source_dset.file.driver == 'mpio':
        if kwargs.pop('compression', None) is not None:
            warn('This HDF5 file has been opened wth the "mpio" communicator. '
                 'mpi4py does not allow creation of compressed datasets. Compression kwarg has been removed')

    if dset_name in h5_group.keys():
        if isinstance(h5_group[dset_name], h5py.Dataset):
            warn('A dataset named: {} already exists in group: {}'.format(dset_name, h5_group.name))
            h5_new_dset = h5_group[dset_name]
            # Make sure it has the correct shape and dtype
            if any((source_dset.shape != h5_new_dset.shape, dtype != h5_new_dset.dtype)):
                warn('Either the shape (existing: {} desired: {}) or dtype (existing: {} desired: {}) of the dataset '
                     'did not match with expectations. Deleting and creating a new one.'.format(h5_new_dset.shape,
                                                                                                source_dset.shape,
                                                                                                h5_new_dset.dtype,
                                                                                                dtype))
                del h5_new_dset, h5_group[dset_name]
                h5_new_dset = h5_group.create_dataset(dset_name, **kwargs)
        else:
            raise KeyError('{} is already a {} in group: {}'.format(dset_name, type(h5_group[dset_name]),
                                                                    h5_group.name))

    else:
        h5_new_dset = h5_group.create_dataset(dset_name, **kwargs)

    # This should link the ancillary datasets correctly
    h5_new_dset = copy_attributes(source_dset, h5_new_dset, skip_refs=skip_refs)
    h5_new_dset.attrs.update(new_attrs)

    if check_if_main(h5_new_dset):
        from .usi_data import USIDataset

        h5_new_dset = USIDataset(h5_new_dset)
        # update book keeping attributes
        write_book_keeping_attrs(h5_new_dset)

    return h5_new_dset


def copy_attributes(source, dest, skip_refs=True):
    # TODO: VERY confusing - why call copy_region_refs() AND copy region refs here???
    """
    Copy attributes from one h5object to another

    Parameters
    ----------
    source : h5py.Dataset, :class:`h5py.Group`, or :class:`h5py.File`
        Object containing the desired attributes
    dest : h5py.Dataset, :class:`h5py.Group`, or :class:`h5py.File`
        Object to which the attributes need to be copied to
    skip_refs : bool, optional. default = True
        Whether or not the references (dataset and region) should be skipped

    """
    if not isinstance(source, (h5py.Dataset, h5py.Group, h5py.File)):
        raise TypeError('source should be a h5py.Dataset, h5py.Group,or h5py.File object')
    if not isinstance(dest, (h5py.Dataset, h5py.Group, h5py.File)):
        raise TypeError('dest should be a h5py.Dataset, h5py.Group, or h5py.File object')

    for att_name in source.attrs.keys():
        att_val = get_attr(source, att_name)
        """
        Don't copy references unless asked
        """
        if isinstance(att_val, h5py.Reference):

            if not skip_refs and not isinstance(dest, h5py.Dataset):
                warn('Skipping region reference named: {}'.format(att_name))
                continue
            elif isinstance(att_val, h5py.RegionReference):
                #     """
                #     Dereference old reference, get the appropriate data
                #     slice and create new reference.
                #     """
                #     try:
                #         region = h5py.h5r.get_region(att_val, source.id)
                #
                #         start, end = region.get_select_bounds()
                #         ref_slice = []
                #         for i in range(len(start)):
                #             if start[i] == end[i]:
                #                 ref_slice.append(start[i])
                #             else:
                #                 ref_slice.append(slice(start[i], end[i]))
                #     except:
                #         warn('Could not copy region reference:{} to {}'.format(att_name, dest.name))
                #         continue
                #
                #     dest.attrs[att_name] = dest.regionref[tuple(ref_slice)]
                continue
            else:
                dest.attrs[att_name] = att_val
                continue

        # everything else
        dest.attrs[att_name] = clean_string_att(att_val)

    if not skip_refs:
        try:
            copy_region_refs(source, dest)
        except TypeError:
            print('Could not copy region references to {}.'.format(dest.name))

    return dest


def check_if_main(h5_main, verbose=False):
    """
    Checks the input dataset to see if it has all the neccessary
    features to be considered a Main dataset.  This means it is
    2D and has the following attributes
    Position_Indices
    Position_Values
    Spectroscopic_Indices
    Spectroscopic_Values

    In addition the shapes of the ancillary matricies should match with that of h5_main

    In addition, it should have the 'quantity' and 'units' attributes

    Parameters
    ----------
    h5_main : HDF5 Dataset
        Dataset of interest
    verbose : Boolean (Optional. Default = False)
        Whether or not to print statements

    Returns
    -------
    success : Boolean
        True if all tests pass

    """
    # Check that h5_main is a dataset
    success = isinstance(h5_main, h5py.Dataset)

    if not success:
        if verbose:
            print('{} is not an HDF5 Dataset object.'.format(h5_main))
        return success

    h5_name = h5_main.name.split('/')[-1]

    # Check dimensionality
    success = np.all([success, len(h5_main.shape) == 2])

    if not success:
        if verbose:
            print('{} is not 2D.'.format(h5_name))
        return success

    # Check for Datasets
    dset_names = ['Position_Indices', 'Position_Values',
                  'Spectroscopic_Indices', 'Spectroscopic_Values']
    for name in dset_names:
        try:
            h5_anc_dset = h5_main.file[h5_main.attrs[name]]
            success = np.all([success, isinstance(h5_anc_dset, h5py.Dataset)])
        except:
            if verbose:
                print('{} not found as an attribute of {}.'.format(name, h5_name))
            return False

    attr_success = np.all([att in h5_main.attrs for att in ['quantity', 'units']])
    if not attr_success:
        if verbose:
            print('{} does not have the mandatory "quantity" and "units" attributes'.format(h5_main.name))
        return False

    # Blindly linking four datasets is still not sufficient. The sizes need to match:
    anc_shape_match = list()
    h5_pos_inds = h5_main.file[h5_main.attrs['Position_Indices']]
    h5_pos_vals = h5_main.file[h5_main.attrs['Position_Values']]
    anc_shape_match.append(np.all(h5_pos_vals.shape == h5_pos_inds.shape))
    for anc_dset in [h5_pos_vals, h5_pos_inds]:
        anc_shape_match.append(np.all(h5_main.shape[0] == anc_dset.shape[0]))
    if not np.all(anc_shape_match):
        if verbose:
            print('The shapes of the Position indices:{}, values:{} datasets did not match with that of the main '
                  'dataset: {}'.format(h5_pos_inds.shape, h5_pos_vals.shape, h5_main.shape))
        return False

    anc_shape_match = list()
    h5_spec_inds = h5_main.file[h5_main.attrs['Spectroscopic_Indices']]
    h5_spec_vals = h5_main.file[h5_main.attrs['Spectroscopic_Values']]
    anc_shape_match.append(np.all(h5_spec_inds.shape == h5_spec_vals.shape))
    for anc_dset in [h5_spec_inds, h5_spec_vals]:
        anc_shape_match.append(np.all(h5_main.shape[1] == anc_dset.shape[1]))
    if not np.all(anc_shape_match):
        if verbose:
            print('The shapes of the Spectroscopic indices:{}, values:{} datasets did not match with that of the main '
                  'dataset: {}'.format(h5_spec_inds.shape, h5_spec_vals.shape, h5_main.shape))
        return False

    return success


def link_h5_objects_as_attrs(src, h5_objects):
    """
    Creates Dataset attributes that contain references to other Dataset Objects.

    Parameters
    -----------
    src : Reference to h5.object
        Reference to the the object to which attributes will be added
    h5_objects : list of references to h5.objects
        objects whose references that can be accessed from src.attrs

    Returns
    --------
    None

    """
    if not isinstance(src, (h5py.Dataset, h5py.File, h5py.Group)):
        raise TypeError('src should either be a h5py Dataset, File, or Group')
    if isinstance(h5_objects, (h5py.Dataset, h5py.Group)):
        h5_objects = [h5_objects]

    for itm in h5_objects:
        if not isinstance(itm, (h5py.Dataset, h5py.Group)):
            raise TypeError('h5_objects should only contain h5py. Dataset and Group objects')
        src.attrs[itm.name.split('/')[-1]] = itm.ref


def link_h5_obj_as_alias(h5_main, h5_ancillary, alias_name):
    """
    Creates Dataset attributes that contain references to other Dataset Objects.
    This function is useful when the reference attribute must have a reserved name.
    Such as linking 'SHO_Indices' as 'Spectroscopic_Indices'

    Parameters
    ------------
    h5_main : h5py.Dataset
        Reference to the the object to which attributes will be added
    h5_ancillary : h5py.Dataset
        object whose reference that can be accessed from src.attrs
    alias_name : String
        Alias / alternate name for trg

    """
    if not isinstance(h5_main, (h5py.Dataset, h5py.File, h5py.Group)):
        raise TypeError('h5_main should either be a h5py Dataset, File, or Group')
    if not isinstance(h5_ancillary, (h5py.Dataset, h5py.Group)):
        raise TypeError('h5_ancillary should be a h5py. Dataset or Group object')
    if not isinstance(alias_name, (str, unicode)):
        raise TypeError('alias_name should be a string')
    alias_name = alias_name.strip()
    h5_main.attrs[alias_name] = h5_ancillary.ref


def link_as_main(h5_main, h5_pos_inds, h5_pos_vals, h5_spec_inds, h5_spec_vals, anc_dsets=None):
    """
    Links the object references to the four position and spectrosocpic datasets as
    attributes of `h5_main`

    Parameters
    ----------
    h5_main : h5py.Dataset
        2D Dataset which will have the references added as attributes
    h5_pos_inds : h5py.Dataset
        Dataset that will be linked with the name 'Position_Indices'
    h5_pos_vals : h5py.Dataset
        Dataset that will be linked with the name 'Position_Values'
    h5_spec_inds : h5py.Dataset
        Dataset that will be linked with the name 'Spectroscopic_Indices'
    h5_spec_vals : h5py.Dataset
        Dataset that will be linked with the name 'Spectroscopic_Values'
    anc_dsets : (Optional) list of h5py.Dataset objects
        Datasets that will be linked with their own names

    """
    # TODO: Make sure that the dimensions of spec and pos match with the data!
    for param, param_name in zip([h5_main, h5_pos_inds, h5_pos_vals, h5_spec_inds, h5_spec_vals],
                                 ['h5_main', 'h5_pos_inds', 'h5_pos_vals', 'h5_spec_inds', 'h5_spec_vals']):
        if not isinstance(param, h5py.Dataset):
            raise TypeError(param_name + ' should be a h5py.Dataset object')

    if h5_pos_vals.shape != h5_pos_inds.shape:
        raise ValueError('h5_pos_vals: {} and h5_pos_inds: {} do not have the same shape'
                         '.'.format(h5_pos_vals.shape, h5_pos_inds))
    if h5_spec_vals.shape != h5_spec_inds.shape:
        raise ValueError('h5_spec_vals: {} and h5_spec_inds: {} do not have the same shape'
                         '.'.format(h5_spec_vals.shape, h5_spec_inds))

    link_h5_obj_as_alias(h5_main, h5_pos_inds, 'Position_Indices')
    link_h5_obj_as_alias(h5_main, h5_pos_vals, 'Position_Values')
    link_h5_obj_as_alias(h5_main, h5_spec_inds, 'Spectroscopic_Indices')
    link_h5_obj_as_alias(h5_main, h5_spec_vals, 'Spectroscopic_Values')

    if anc_dsets is not None:
        if not isinstance(anc_dsets, (list, tuple)):
            raise TypeError('anc_dsets should be a list or tuple')
        anc_dsets = list(anc_dsets)
        np.all([isinstance(item, h5py.Dataset) for item in anc_dsets])

        for dset in anc_dsets:
            link_h5_objects_as_attrs(h5_main, dset)


def copy_main_attributes(h5_main, h5_new):
    """
    Copies the units and quantity name from one dataset to another

    Parameters
    ----------
    h5_main : h5py.Dataset
        Dataset containing the target attributes
    h5_new : h5py.Dataset
        Dataset to which the target attributes are to be copied

    """
    for param, param_name in zip([h5_main, h5_new], ['h5_main', 'h5_new']):
        if not isinstance(param, h5py.Dataset):
            raise TypeError(param_name + ' should be a h5py.Dataset object')

    for att_name in ['quantity', 'units']:
        if att_name not in h5_main.attrs:
            raise KeyError('Attribute: {} does not exist in {}'.format(att_name, h5_main))
        val = get_attr(h5_main, att_name)
        h5_new.attrs[att_name] = clean_string_att(val)


def check_for_old(h5_base, tool_name, new_parms=None, target_dset=None, verbose=False):
    """
    Check to see if the results of a tool already exist and if they 
    were performed with the same parameters.
    
    Parameters
    ----------
    h5_base : h5py.Dataset object
           Dataset on which the tool is being applied to
    tool_name : str
           process or analysis name
    new_parms : dict, optional
           Parameters with which this tool will be performed.
    target_dset : str, optional, default = None
            Name of the dataset whose attributes will be compared against new_parms.
            Default - checking against the group
    verbose : bool, optional, default = False
           Whether or not to print debugging statements 
           
    Returns
    -------
    group : list
           List of all :class:`h5py.Group` objects with parameters matching those in `new_parms`

    """
    if not isinstance(h5_base, h5py.Dataset):
        raise TypeError('h5_base should be a h5py.Dataset object')
    if not isinstance(tool_name, (str, unicode)):
        raise TypeError('tool_name should be a string')
    if new_parms is None:
        new_parms = dict()
    else:
        if not isinstance(new_parms, dict):
            raise TypeError('new_parms should be a dict')
    if target_dset is not None:
        if not isinstance(target_dset, (str, unicode)):
            raise TypeError('target_dset should be a string')

    matching_groups = []
    groups = find_results_groups(h5_base, tool_name)

    for group in groups:
        if verbose:
            print('Looking at group - {}'.format(group.name.split('/')[-1]))

        h5_obj = group
        if target_dset is not None:
            if target_dset in group.keys():
                h5_obj = group[target_dset]
            else:
                if verbose:
                    print('{} did not contain the target dataset: {}'.format(group.name.split('/')[-1],
                                                                             target_dset))
                continue

        if check_for_matching_attrs(h5_obj, new_parms=new_parms, verbose=verbose):
            # return group
            matching_groups.append(group)

    return matching_groups


def check_for_matching_attrs(h5_obj, new_parms=None, verbose=False):
    """
    Compares attributes in the given H5 object against those in the provided dictionary and returns True if
    the parameters match, and False otherwise

    Parameters
    ----------
    h5_obj : h5py object (Dataset or :class:`h5py.Group`)
        Object whose attributes will be compared against new_parms
    new_parms : dict, optional. default = empty dictionary
        Parameters to compare against the attributes present in h5_obj
    verbose : bool, optional, default = False
       Whether or not to print debugging statements

    Returns
    -------
    tests: bool
        Whether or not all paramters in new_parms matched with those in h5_obj's attributes

    """
    if not isinstance(h5_obj, (h5py.Dataset, h5py.Group, h5py.File)):
        raise TypeError('h5_obj should be a h5py.Dataset, h5py.Group, or h5py.File object')
    if new_parms is None:
        new_parms = dict()
    else:
        if not isinstance(new_parms, dict):
            raise TypeError('new_parms should be a dictionary')

    tests = []
    for key in new_parms.keys():

        if verbose:
            print('Looking for new attribute named: {}'.format(key))

        # HDF5 cannot store None as an attribute anyway. ignore
        if new_parms[key] is None:
            continue

        try:
            old_value = get_attr(h5_obj, key)
        except KeyError:
            # if parameter was not found assume that something has changed
            if verbose:
                print('New parm: {} \t- new parm not in group *****'.format(key))
            tests.append(False)
            break

        if isinstance(old_value, np.ndarray):
            if not isinstance(new_parms[key], collections.Iterable):
                if verbose:
                    print('New parm: {} \t- new parm not iterable unlike old parm *****'.format(key))
                tests.append(False)
                break
            new_array = np.array(new_parms[key])
            if old_value.size != new_array.size:
                if verbose:
                    print('New parm: {} \t- are of different sizes ****'.format(key))
                tests.append(False)
            else:
                try:
                    answer = np.allclose(old_value, new_array)
                except TypeError:
                    # comes here when comparing string arrays
                    # Not sure of a better way
                    answer = []
                    for old_val, new_val in zip(old_value, new_array):
                        answer.append(old_val == new_val)
                    answer = np.all(answer)
                if verbose:
                    print('New parm: {} \t- match: {}'.format(key, answer))
                tests.append(answer)
        else:
            """if isinstance(new_parms[key], collections.Iterable):
                if verbose:
                    print('New parm: {} \t- new parm is iterable unlike old parm *****'.format(key))
                tests.append(False)
                break"""
            answer = np.all(new_parms[key] == old_value)
            if verbose:
                print('New parm: {} \t- match: {}'.format(key, answer))
            tests.append(answer)
    if verbose:
        print('')

    return all(tests)


def get_unit_values(ds_inds, ds_vals, dim_names=None, all_dim_names=None, is_spec=None, verbose=False):
    """
    Gets the unit arrays of values that describe the spectroscopic dimensions

    Parameters
    ----------
    ds_inds : h5py.Dataset or numpy.ndarray
        Spectroscopic or Position Indices dataset
    ds_vals : h5py.Dataset or numpy.ndarray
        Spectroscopic or Position Values dataset
    dim_names : str, or list of str, Optional
        Names of the dimensions of interest. Default = all
    all_dim_names : list of str, Optional
        Names of all the dimensions in these datasets. Use this if supplying numpy arrays instead of h5py.Dataset
        objects for h5_inds, h5_vals since there is no other way of getting the dimension names.
    is_spec : bool, optional
        Whether or not the provided ancillary datasets are position or spectroscopic
        The user is recommended to supply this parameter whenever it is known
        By default, this function will attempt to recognize the answer based on the shape of the datasets.
    verbose : bool, optional
        Whether or not to print debugging statements. Default - off

    Note - this function can be extended / modified for ancillary position dimensions as well

    Returns
    -------
    unit_values : dict
        Dictionary containing the unit array for each dimension. The name of the dimensions are the keys.

    """
    if all_dim_names is None:
        allowed_types = h5py.Dataset
    else:
        if isinstance(all_dim_names, (list, tuple)):
            if not np.all([isinstance(obj, (str, unicode)) for obj in all_dim_names]):
                raise TypeError('all_dim_names should be a list of strings')
        all_dim_names = np.array(all_dim_names)
        allowed_types = (h5py.Dataset, np.ndarray)

    for dset, dset_name in zip([ds_inds, ds_vals], ['ds_inds', 'ds_vals']):
        if not isinstance(dset, allowed_types):
            raise TypeError(dset_name + ' should be of type: {}'.format(allowed_types))

    # For now, we will throw an error if even a single dimension is listed as an incomplete dimension:
    if isinstance(ds_inds, h5py.Dataset):
        if np.any(['incomplete_dimensions' in dset.attrs.keys() for dset in [ds_inds, ds_vals]]):
            try:
                incomp_dims_inds = get_attr(ds_inds, 'incomplete_dimensions')
            except KeyError:
                incomp_dims_inds = None
            try:
                incomp_dims_vals = get_attr(ds_vals, 'incomplete_dimensions')
            except KeyError:
                incomp_dims_vals = None
            if incomp_dims_inds is None and incomp_dims_vals is not None:
                incomp_dims = incomp_dims_vals
            elif incomp_dims_inds is not None and incomp_dims_vals is None:
                incomp_dims = incomp_dims_inds
            else:
                # ensure that both attributes are the same
                if incomp_dims_vals != incomp_dims_inds:
                    raise ValueError('Provided indices ({}) and values ({}) datasets were marked with different values '
                                     'for incomplete_datasets.'.format(incomp_dims_inds, incomp_dims_vals))
                incomp_dims = incomp_dims_vals

            all_dim_names = get_attr(ds_inds, 'labels')
            raise ValueError('Among all dimensions: {}, These dimensions were marked as incomplete dimensions: {}'
                             '. You are recommended to find unit values manually'.format(all_dim_names, incomp_dims))

    # Do we need to check that the provided inds and vals correspond to the same main dataset?
    if ds_inds.shape != ds_vals.shape:
        raise ValueError('h5_inds: {} and h5_vals: {} should have the same shapes'.format(ds_inds.shape, ds_vals.shape))

    if all_dim_names is None:
        all_dim_names = get_attr(ds_inds, 'labels')
    if verbose:
        print('All dimensions: {}'.format(all_dim_names))

    # First load to memory
    inds_mat = ds_inds[()]
    vals_mat = ds_vals[()]
    
    if is_spec is None:
        # Attempt to recognize the type automatically
        is_spec = False
        if inds_mat.shape[0] < inds_mat.shape[1]:
            is_spec = True
    else:
        if not isinstance(is_spec, bool):
            raise TypeError('is_spec should be a boolean. Provided object is of type: {}'.format(type(is_spec)))

    if verbose:
        print(
            'Ancillary matrices of shape: {}, hence determined to be Spectroscopic:{}'.format(inds_mat.shape, is_spec))

    if not is_spec:
        # Convert to spectral shape
        inds_mat = np.transpose(inds_mat)
        vals_mat = np.transpose(vals_mat)

    if len(all_dim_names) != inds_mat.shape[0]:
        raise ValueError('Length of dimension names list: {} not matching with shape of dataset: {}'
                         '.'.format(len(all_dim_names), inds_mat.shape[0]))

    if dim_names is None:
        dim_names = all_dim_names
        if verbose:
            print('Going to return unit values for all dimensions: {}'.format(all_dim_names))
    else:
        if isinstance(dim_names, (str, unicode)):
            dim_names = [dim_names]
        if not isinstance(dim_names, (list, tuple)):
            raise TypeError('dim_names should be a list or tuple')

        if verbose:
            print('Checking to make sure that the target dimension names: {} exist in the datasets attributes: {}'
                  '.'.format(dim_names, all_dim_names))

        # check to make sure that the dimension names exist in the datasets:
        for dim_name in dim_names:
            if not isinstance(dim_name, (str, unicode)):
                raise TypeError('dim_name should be a string')
            if dim_name not in all_dim_names:
                raise KeyError('Dimension {} does not exist in the provided ancillary datasets'.format(dim_name))

    unit_values = dict()
    for dim_name in all_dim_names:
        # Find the row in the spectroscopic indices that corresponds to the dimensions we want to slice:
        if verbose:
            print('Looking for dimension: {} in {}'.format(dim_name, dim_names))
        desired_row_ind = np.where(all_dim_names == dim_name)[0][0]

        inds_for_dim = inds_mat[desired_row_ind]
        # Wherever this dimension goes to 0 - start of a new tile
        starts = np.where(inds_for_dim == np.min(inds_for_dim))[0]
        # There may be repetitions in addition to tiling. Find how the the positions increase.
        # 1 = repetition, > 1 = new tile
        step_sizes = np.hstack(([1], np.diff(starts)))
        # This array is of the same length as the full indices array

        # We should expect only two values of step sizes for a regular dimension (tiles of the same size):
        # 1 for same value repeating and a big jump in indices when the next tile starts
        # If the repeats / tiles are of different lengths, then this is not a regular dimension.
        # What does a Unit Values vector even mean in this case? Just raise an error for now
        if np.where(np.unique(step_sizes) - 1)[0].size > 1:
            raise ValueError('Non constant step sizes')

        # Finding Start of a new tile
        tile_starts = np.where(step_sizes > 1)[0]

        # converting these indices to correct indices that can be mapped straight to
        if len(tile_starts) < 1:
            # Dimension(s) with no tiling at all
            # Make it look as though the next tile starts at the end of the whole indices vector
            tile_starts = np.array([0, len(inds_for_dim)])
        else:
            # Dimension with some form of repetition
            tile_starts = np.hstack(([0], starts[tile_starts]))

            # Verify that each tile is identical here
            # Last tile will not be checked unless we add the length of the indices vector as the start of next tile
            tile_starts = np.hstack((tile_starts, [len(inds_for_dim)]))
            subsections = [inds_for_dim[tile_starts[ind]: tile_starts[ind + 1]] for ind in range(len(tile_starts) - 1)]
            if np.max(np.diff(subsections, axis=0)) != 0:
                # Should get unit values for ALL dimensions regardless of expectations to catch such scenarios.
                raise ValueError('Values in each tile of dimension: {} are different'.format(dim_name))

        # Now looking within the first tile:
        subsection = inds_for_dim[tile_starts[0]:tile_starts[1]]
        # remove all repetitions. ie - take indices only where jump == 1
        step_inds = np.hstack(([0], np.where(np.hstack(([0], np.diff(subsection))))[0]))
        # Finally, use these indices to get the values
        if dim_name in dim_names:
            # Only add this dimension to dictionary if requwested.
            unit_values[dim_name] = vals_mat[desired_row_ind, step_inds]

    return unit_values


def get_source_dataset(h5_group):
    """
    Find the name of the source dataset used to create the input `h5_group`

    Parameters
    ----------
    h5_group : :class:`h5py.Group`
        Child group whose source dataset will be returned

    Returns
    -------
    h5_source : USIDataset object
        Main dataset from which this group was generated

    """
    if not isinstance(h5_group, h5py.Group):
        raise TypeError('h5_group should be a h5py.Group object')

    from .usi_data import USIDataset

    h5_parent_group = h5_group.parent
    group_name = h5_group.name.split('/')[-1]
    # What if the group name was not formatted according to Pycroscopy rules?
    name_split = group_name.split('-')
    if len(name_split) != 2:
        raise ValueError("The provided group's name could not be split by '-' as expected in "
                         "SourceDataset-ProcessName_000")
    h5_source = h5_parent_group[name_split[0]]

    if not isinstance(h5_source, h5py.Dataset):
        raise ValueError('Source object was not a dataset!')

    return USIDataset(h5_source)


def is_editable_h5(h5_obj):
    """
    Returns True if the file containing the provided h5 object is in w or r+ modes

    Parameters
    ----------
    h5_obj : h5py.File, h5py.Group, or h5py.Dataset object
        h5py object

    Returns
    -------
    mode : bool
        True if the file containing the provided h5 object is in w or r+ modes

    """
    if not isinstance(h5_obj, (h5py.File, h5py.Group, h5py.Dataset)):
        raise TypeError('h5_obj should be a h5py File, Group or Dataset object but is instead of type '
                        '{}t'.format(type(h5_obj)))
    try:
        file_handle = h5_obj.file
    except RuntimeError:
        raise ValueError('Encountered a RuntimeError possibly due to a closed file')
    # file handle is actually an open hdf file
    try:
        _ = file_handle.mode
    except ValueError:
        raise ValueError('A closed h5py.File was provided')

    if file_handle.mode == 'r':
        return False
    return True


def write_ind_val_dsets(h5_parent_group, dimensions, is_spectral=True, verbose=False, base_name=None):
    """
    Creates h5py.Datasets for the position OR spectroscopic indices and values of the data.
    Remember that the contents of the dataset can be changed if need be after the creation of the datasets.
    For example if one of the spectroscopic dimensions (e.g. - Bias) was sinusoidal and not linear, The specific
    dimension in the Spectroscopic_Values dataset can be manually overwritten.

    Parameters
    ----------
    h5_parent_group : :class:`h5py.Group` or :class:`h5py.File`
        Group under which the indices and values datasets will be created
    dimensions : Dimension or array-like of Dimension objects
        Sequence of Dimension objects that provides all necessary instructions for constructing the indices and values
        datasets
    is_spectral : bool, optional. default = True
        Spectroscopic (True) or Position (False)
    verbose : Boolean, optional
        Whether or not to print statements for debugging purposes
    base_name : str or unicode, optional
        Prefix for the datasets. Default: 'Position' when is_spectral is False, 'Spectroscopic' otherwise

    Returns
    -------
    h5_spec_inds : h5py.Dataset
        Dataset containing the position indices
    h5_spec_vals : h5py.Dataset
        Dataset containing the value at each position

    Notes
    -----
    `steps`, `initial_values`, `labels`, and 'units' must be the same length as
    `dimensions` when they are specified.

    Dimensions should be in the order from fastest varying to slowest.

    """
    if isinstance(dimensions, Dimension):
        dimensions = [dimensions]
    if not isinstance(dimensions, (list, np.ndarray, tuple)):
        raise TypeError('dimensions should be array-like ')
    if not np.all([isinstance(x, Dimension) for x in dimensions]):
        raise TypeError('dimensions should be a sequence of Dimension objects')

    if not isinstance(h5_parent_group, (h5py.Group, h5py.File)):
        raise TypeError('h5_parent_group should be a h5py.File or Group object')
    if not is_editable_h5(h5_parent_group):
        raise ValueError('The provided h5 object is not valid / open')

    if base_name is not None:
        if not isinstance(base_name, (str, unicode)):
            raise TypeError('base_name should be a string')
        if not base_name.endswith('_'):
            base_name += '_'
    else:
        base_name = 'Position_'
        if is_spectral:
            base_name = 'Spectroscopic_'

    # check if the datasets already exist. If they do, there's no point in going any further
    for sub_name in ['Indices', 'Values']:
        if base_name + sub_name in h5_parent_group.keys():
            raise KeyError('Dataset: {} already exists in provided group: {}'.format(base_name + sub_name,
                                                                                     h5_parent_group.name))

    unit_values = [x.values for x in dimensions]

    indices, values = build_ind_val_matrices(unit_values, is_spectral=is_spectral)

    if verbose:
        print('Indices:')
        print(indices)
        print('Values:')
        print(values)

    # Create the slices that will define the labels
    region_slices = get_aux_dset_slicing([x.name for x in dimensions], is_spectroscopic=is_spectral)

    # Create the Datasets for both Indices and Values
    h5_indices = h5_parent_group.create_dataset(base_name + 'Indices', data=INDICES_DTYPE(indices), dtype=INDICES_DTYPE)
    h5_values = h5_parent_group.create_dataset(base_name + 'Values', data=VALUES_DTYPE(values), dtype=VALUES_DTYPE)

    for h5_dset in [h5_indices, h5_values]:
        write_region_references(h5_dset, region_slices, verbose=verbose)
        write_simple_attrs(h5_dset, {'units': [x.units for x in dimensions], 'labels': [x.name for x in dimensions]})

    return h5_indices, h5_values


def write_reduced_spec_dsets(h5_parent_group, h5_spec_inds, h5_spec_vals, dim_name, basename='Spectroscopic',
                             verbose=False):
    """
    Creates new Spectroscopic Indices and Values datasets from the input datasets
    and keeps the dimensions specified in keep_dim

    Parameters
    ----------
    h5_parent_group : :class:`h5py.Group` or :class:`h5py.File`
        Group under which the indices and values datasets will be created
    h5_spec_inds : HDF5 Dataset
            Spectroscopic indices dataset
    h5_spec_vals : HDF5 Dataset
            Spectroscopic values dataset
    dim_name : str or unicode or list of strings
            Names of the dimension(s) to remove
    basename : str or unicode, Optional
            String to which '_Indices' and '_Values' will be appended to get the names
            of the new datasets
    verbose : bool, optional. Default = False
            Whether or not to print debugging print statements

    Returns
    -------
    h5_inds : h5py.Dataset
            Reduced Spectroscopic indices dataset
    h5_vals : h5py.Dataset
            Reduces Spectroscopic values dataset

    """
    warn('write_reduced_spec_dsets is deprecated. Please use write_reduced_anc_dsets instead', FutureWarning)
    return write_reduced_anc_dsets(h5_parent_group, h5_spec_inds, h5_spec_vals, dim_name, basename=basename,
                                   verbose=verbose)


def write_reduced_anc_dsets(h5_parent_group, h5_inds, h5_vals, dim_name, basename=None, is_spec=None,
                            verbose=False):
    """
    Creates new Ancillary Indices and Values datasets from the input datasets by dropping the specified dimensions

    Parameters
    ----------
    h5_parent_group : :class:`h5py.Group` or h5py.File
        Group under which the indices and values datasets will be created
    h5_inds : HDF5 Dataset
            Spectroscopic or Positions indices dataset
    h5_vals : HDF5 Dataset
            Spectroscopic or Positions values dataset
    dim_name : str or unicode or list of strings
            Names of the dimension(s) to remove
    basename : str or unicode, Optional
            String to which '_Indices' and '_Values' will be appended to get the names of the new datasets.
            Default = 'Position' or 'Spectroscopic'
    is_spec : bool, optional
            Whether or not the provided ancillary datasets are position or spectroscopic
            The user is recommended to supply this parameter whenever it is known or possible.
            By default, this function will attempt to recognize the answer based on the shape of the datasets.
    verbose : bool, optional. Default = False
            Whether or not to print debugging print statements

    Returns
    -------
    h5_inds_new : h5py.Dataset
            Reduced indices dataset
    h5_vals_new : h5py.Dataset
            Reduces values dataset

    """
    if not isinstance(h5_parent_group, (h5py.Group, h5py.File)):
        raise TypeError('h5_parent_group should either be a h5py. Group or File object')

    for param, param_name in zip([h5_inds, h5_vals], ['h5_inds', 'h5_vals']):
        if not isinstance(param, h5py.Dataset):
            raise TypeError(param_name + ' should be a h5py.Dataset object')
    if dim_name is not None:
        if isinstance(dim_name, (str, unicode)):
            dim_name = [dim_name]

        if not isinstance(dim_name, (list, tuple)):
            raise TypeError('dim_name should either be a string or a list of strings')
        if not np.all([isinstance(item, (str, unicode)) for item in dim_name]):
            raise TypeError('dim_name should either be a string or a list of strings')

    all_dim_names = list(get_attr(h5_inds, 'labels'))
    for item in dim_name:
        if item not in all_dim_names:
            raise KeyError('Requested dimension: {} not in the list of labels: {}'.format(item, all_dim_names))

    ind_mat = h5_inds[()]
    val_mat = h5_vals[()]

    if is_spec is None:
        # Attempt to recognize the type automatically
        is_spec = False
        if ind_mat.shape[0] == ind_mat.shape[1]:
            raise ValueError('Unable automatically guess whether the provided datasets are position or '
                             'spectroscopic. Please explicitely specify via the "is_spec" boolean kwarg')
        if ind_mat.shape[0] < ind_mat.shape[1]:
            is_spec = True
    else:
        if not isinstance(is_spec, bool):
            raise TypeError('is_spec should be a boolean. Provided object is of type: {}'.format(type(is_spec)))

    if basename is not None:
        if not isinstance(basename, (str, unicode)):
            raise TypeError('basename should be a string')
        if basename.endswith('_'):
            basename = basename[:-1]
    else:
        if is_spec:
            basename = 'Spectroscopic'
        else:
            basename = 'Position'

    for sub_name in ['_Indices', '_Values']:
        if basename + sub_name in h5_parent_group.keys():
            raise KeyError('Dataset: {} already exists in provided group: {}'.format(basename + sub_name,
                                                                                     h5_parent_group.name))

    if set(dim_name) != set(all_dim_names):
        # At least one dimension will remain

        if verbose:
            print('All Dimensions: {}. Dimensions to be removed: {}'.format(all_dim_names, dim_name))

        if not is_spec:
            # Convert to spectral shape
            ind_mat = np.transpose(ind_mat)
            val_mat = np.transpose(val_mat)

        # For all dimensions, find where the index = 0
        # basically, we are indexing all dimensions to 0
        first_indices = []
        keep_dim = np.ones(len(all_dim_names), dtype=bool)
        for cur_dim in dim_name:
            dim_ind = all_dim_names.index(cur_dim)
            keep_dim[dim_ind] = False
            # check equality against the minimum value instead of 0 to account for cases when a dimension does not start
            # from 0 (already been sliced) - think of multi-dimensional slicing!
            first_indices.append(ind_mat[dim_ind] == np.min(ind_mat[dim_ind]))
        first_indices = np.vstack(first_indices)

        if verbose:
            print('Raw first_indices:')
            print(first_indices)
            print('Dimensions to keep: {}'.format(keep_dim))

        step_starts = np.all(first_indices, axis=0)

        if verbose:
            print('Columns in dataset to keep:')
            print(step_starts)

        '''
        Extract all rows that we want to keep from input indices and values
        '''
        # TODO: handle TypeError: Indexing elements must be in increasing order
        ind_mat = ind_mat[keep_dim, :][:, step_starts]
        val_mat = val_mat[keep_dim, :][:, step_starts]

        if not is_spec:
            # Convert back to position shape
            ind_mat = np.transpose(ind_mat)
            val_mat = np.transpose(val_mat)

        '''
        Create new Datasets to hold the data
        Name them based on basename
        '''
        h5_inds_new = h5_parent_group.create_dataset(basename + '_Indices', data=ind_mat, dtype=h5_inds.dtype)
        h5_vals_new = h5_parent_group.create_dataset(basename + '_Values', data=val_mat, dtype=h5_vals.dtype)
        # Extracting the labels from the original spectroscopic data sets
        labels = h5_inds.attrs['labels'][keep_dim]
        # Creating the dimension slices for the new spectroscopic data sets
        reg_ref_slices = dict()
        for row_ind, row_name in enumerate(labels):
            # Not necessary anymore but still.....
            if is_spec:
                reg_ref_slices[row_name] = (slice(row_ind, row_ind + 1), slice(None))
            else:
                reg_ref_slices[row_name] = (slice(None), slice(row_ind, row_ind + 1))

            if verbose:
                print(reg_ref_slices)

        # Adding the labels and units to the new spectroscopic data sets
        for dset in [h5_inds_new, h5_vals_new]:
            write_region_references(dset, reg_ref_slices, verbose=False)
            write_simple_attrs(dset, {'labels': labels, 'units': h5_inds.attrs['units'][keep_dim]})

    else:
        # Remove all dimensions:
        h5_inds_new = h5_parent_group.create_dataset(basename + '_Indices', data=np.array([[0]]), dtype=INDICES_DTYPE)
        h5_vals_new = h5_parent_group.create_dataset(basename + '_Values', data=np.array([[0]]), dtype=VALUES_DTYPE)

        reg_ref_slices = {'Single_Step': (slice(0, None), slice(None))}

        for dset in [h5_inds_new, h5_vals_new]:
            write_region_references(dset, reg_ref_slices, verbose=False)
            write_simple_attrs(dset, {'labels': ['Single_Step'], 'units': ['a. u.']})

    return h5_inds_new, h5_vals_new


def assign_group_index(h5_parent_group, base_name, verbose=False):
    """
    Searches the parent h5 group to find the next available index for the group

    Parameters
    ----------
    h5_parent_group : :class:`h5py.Group` object
        Parent group under which the new group object will be created
    base_name : str or unicode
        Base name of the new group without index
    verbose : bool, optional. Default=False
        Whether or not to print debugging statements

    Returns
    -------
    base_name : str or unicode
        Base name of the new group with the next available index as a suffix

    """
    if not isinstance(h5_parent_group, h5py.Group):
        raise TypeError('h5_parent_group should be a h5py.Group object')
    if not isinstance(base_name, (str, unicode)):
        raise TypeError('base_name should be a string')

    if len(base_name) == 0:
        raise ValueError('base_name should not be an empty string')

    if not base_name.endswith('_'):
        base_name += '_'

    temp = [key for key in h5_parent_group.keys()]
    if verbose:
        print('Looking for group names starting with {} in parent containing items: '
              '{}'.format(base_name, temp))
    previous_indices = []
    for item_name in temp:
        if isinstance(h5_parent_group[item_name], h5py.Group) and item_name.startswith(base_name):
            previous_indices.append(int(item_name.replace(base_name, '')))
    previous_indices = np.sort(previous_indices)
    if verbose:
        print('indices of existing groups with the same prefix: {}'.format(previous_indices))
    if len(previous_indices) == 0:
        index = 0
    else:
        index = previous_indices[-1] + 1
    return base_name + '{:03d}'.format(index)


def create_indexed_group(h5_parent_group, base_name):
    """
    Creates a group with an indexed name (eg - 'Measurement_012') under h5_parent_group using the provided base_name
    as a prefix for the group's name

    Parameters
    ----------
    h5_parent_group : :class:`h5py.Group` or :class:`h5py.File`
        File or group within which the new group will be created
    base_name : str or unicode
        Prefix for the group name. This need not end with a '_'. It will be added automatically

    Returns
    -------

    """
    if not isinstance(h5_parent_group, (h5py.Group, h5py.File)):
        raise TypeError('h5_parent_group should be a h5py.File or Group object')
    if not isinstance(base_name, (str, unicode)):
        raise TypeError('base_name should be a string')
    base_name = base_name.strip()
    if len(base_name) == 0:
        raise ValueError('base_name should not be an empty string')
    group_name = assign_group_index(h5_parent_group, base_name)
    h5_new_group = h5_parent_group.create_group(group_name)
    write_book_keeping_attrs(h5_new_group)
    return h5_new_group


def write_book_keeping_attrs(h5_obj):
    """
    Writes basic book-keeping and posterity related attributes to groups created in pyUSID such as machine id,
    pyUSID version, timestamp.

    Parameters
    ----------
    h5_obj : :class:`h5py.Dataset`, :class:`h5py.Group`, or :class:`h5py.File`
        Object to which basic book-keeping attributes need to be written

    """
    if not isinstance(h5_obj, (h5py.Group, h5py.File, h5py.Dataset)):
        raise TypeError('h5_obj should be a h5py.Group, h5py.File, or h5py.Dataset object')
    write_simple_attrs(h5_obj, {'machine_id': socket.getfqdn(),
                                'timestamp': get_time_stamp(),
                                'pyUSID_version': py_usid_version,
                                'platform': platform()},
                       verbose=False)


def create_results_group(h5_main, tool_name):
    """
    Creates a h5py.Group object autoindexed and named as 'DatasetName-ToolName_00x'

    Parameters
    ----------
    h5_main : h5py.Dataset object
        Reference to the dataset based on which the process / analysis is being performed
    tool_name : string / unicode
        Name of the Process / Analysis applied to h5_main

    Returns
    -------
    h5_group : :class:`h5py.Group`
        Results group which can now house the results datasets

    """
    if not isinstance(h5_main, h5py.Dataset):
        raise TypeError('h5_main should be a h5py.Dataset or Pycrodataset object')
    if not isinstance(tool_name, (str, unicode)):
        raise TypeError('tool_name should be a string')
    tool_name = tool_name.strip()
    if len(tool_name) < 1:
        raise ValueError('tool_name should not be an empty string')

    if '-' in tool_name:
        warn('tool_name should not contain the "-" character. Reformatted name from:{} to '
             '{}'.format(tool_name, tool_name.replace('-', '_')))
    tool_name = tool_name.replace('-', '_')

    group_name = h5_main.name.split('/')[-1] + '-' + tool_name + '_'
    group_name = assign_group_index(h5_main.parent, group_name)

    h5_group = h5_main.parent.create_group(group_name)

    write_book_keeping_attrs(h5_group)

    # Also add some basic attributes like source and tool name. This will allow relaxation of nomenclature restrictions:
    # this are NOT being used right now but will be in the subsequent versions of pyUSID
    write_simple_attrs(h5_group, {'tool': tool_name, 'num_source_dsets': 1})
    # in this case, there is only one source
    for dset_ind, dset in enumerate([h5_main]):
        h5_group.attrs['source_' + '{:03d}'.format(dset_ind)] = dset.ref

    return h5_group


def write_simple_attrs(h5_obj, attrs, obj_type='', verbose=False):
    """
    Writes attributes to a h5py object

    Parameters
    ----------
    h5_obj : :class:`h5py.File`, :class:`h5py.Group`, or h5py.Dataset object
        h5py object to which the attributes will be written to
    attrs : dict
        Dictionary containing the attributes as key-value pairs
    obj_type : str or unicode, optional. Default = ''
        type of h5py.obj. Examples include 'group', 'file', 'dataset
    verbose : bool, optional. Default=False
        Whether or not to print debugging statements

    """
    if not isinstance(attrs, dict):
        raise TypeError('attrs should be a dictionary but is instead of type '
                        '{}'.format(type(attrs)))
    if not isinstance(h5_obj, (h5py.File, h5py.Group, h5py.Dataset)):
        raise TypeError('h5_obj should be a h5py File, Group or Dataset object but is instead of type '
                        '{}t'.format(type(h5_obj)))

    for key, val in attrs.items():
        if val is None:
            continue
        if verbose:
            print('Writing attribute: {} with value: {}'.format(key, val))
        clean_val = clean_string_att(val)
        if verbose:
            print('Attribute cleaned into: {}'.format(clean_val))
        h5_obj.attrs[key] = clean_val
    if verbose:
        print('Wrote all (simple) attributes to {}: {}\n'.format(obj_type, h5_obj.name.split('/')[-1]))


def write_main_dataset(h5_parent_group, main_data, main_data_name, quantity, units, pos_dims, spec_dims,
                       main_dset_attrs=None, h5_pos_inds=None, h5_pos_vals=None, h5_spec_inds=None, h5_spec_vals=None,
                       aux_spec_prefix='Spectroscopic_', aux_pos_prefix='Position_', verbose=False, **kwargs):
    """
    Writes the provided data as a 'Main' dataset with all appropriate linking.
    By default, the instructions for generating the ancillary datasets should be specified using the pos_dims and
    spec_dims arguments as dictionary objects. Alternatively, if both the indices and values datasets are already
    available for either/or the positions / spectroscopic, they can be specified using the keyword arguments. In this
    case, fresh datasets will not be generated.

    Parameters
    ----------
    h5_parent_group : :class:`h5py.Group`
        Parent group under which the datasets will be created
    main_data : numpy.ndarray, dask.array.core.Array, list or tuple
        2D matrix formatted as [position, spectral] or a list / tuple with the shape for an empty dataset.
        If creating an empty dataset - the dtype must be specified via a kwarg.
    main_data_name : String / Unicode
        Name to give to the main dataset. This cannot contain the '-' character.
    quantity : String / Unicode
        Name of the physical quantity stored in the dataset. Example - 'Current'
    units : String / Unicode
        Name of units for the quantity stored in the dataset. Example - 'A' for amperes
    pos_dims : Dimension or array-like of Dimension objects
        Sequence of Dimension objects that provides all necessary instructions for constructing the indices and values
        datasets
        Object specifying the instructions necessary for building the Position indices and values datasets
    spec_dims : Dimension or array-like of Dimension objects
        Sequence of Dimension objects that provides all necessary instructions for constructing the indices and values
        datasets
        Object specifying the instructions necessary for building the Spectroscopic indices and values datasets
    main_dset_attrs : dictionary, Optional
        Dictionary of parameters that will be written to the main dataset. Do NOT include region references here.
    h5_pos_inds : h5py.Dataset, Optional
        Dataset that will be linked with the name "Position_Indices"
    h5_pos_vals : h5py.Dataset, Optional
        Dataset that will be linked with the name "Position_Values"
    h5_spec_inds : h5py.Dataset, Optional
        Dataset that will be linked with the name "Spectroscopic_Indices"
    h5_spec_vals : h5py.Dataset, Optional
        Dataset that will be linked with the name "Spectroscopic_Values"
    aux_spec_prefix : str or unicode, Optional
        Default prefix for Spectroscopic datasets. Default = "Spectroscopic"
    aux_pos_prefix : str or unicode, Optional
        Default prefix for Position datasets. Default = "Position"
    verbose : bool, Optional, default=False
        If set to true - prints debugging logs
    kwargs will be passed onto the creation of the dataset. Please pass chunking, compression, dtype, and other
        arguments this way

    Returns
    -------
    h5_main : USIDataset
        Reference to the main dataset
        
    """

    def __validate_anc_h5_dsets(inds, vals, main_shape, is_spectroscopic=True):
        if not isinstance(inds, h5py.Dataset):
            raise TypeError('inds must be a h5py.Dataset object')
        if not isinstance(vals, h5py.Dataset):
            raise TypeError('vals must be a h5py.Dataset object')
        if inds.shape != vals.shape:
            raise ValueError('inds: {} and vals: {} should be of the same shape'.format(inds.shape, vals.shape))
        if inds.shape[is_spectroscopic] != main_shape[is_spectroscopic]:
            raise ValueError('index {} in shape of inds: {} and main_data: {} should be equal'
                             '.'.format(int(is_spectroscopic), inds.shape, main_shape))

    def __validate_dimensions(dimensions, dim_type='Position'):
        if isinstance(dimensions, Dimension):
            dimensions = [dimensions]
        if not isinstance(dimensions, (list, np.ndarray, tuple)):
            raise TypeError(dim_type + ' dimensions should be array-like ')
        if not np.all([isinstance(x, Dimension) for x in dimensions]):
            raise TypeError(dim_type + ' dimensions should be a sequence of Dimension objects')
        return dimensions

    def __check_anc_before_creation(aux_prefix, dim_type='pos'):
        if not isinstance(aux_prefix, (str, unicode)):
            raise TypeError('aux_' + dim_type + '_prefix should be a string')
        aux_prefix = aux_prefix.strip()
        if not aux_prefix.endswith('_'):
            aux_prefix += '_'
        if '-' in aux_prefix:
            warn('aux_' + dim_type + ' should not contain the "-" character. Reformatted name from:{} to '
                                     '{}'.format(aux_prefix, aux_prefix.replace('-', '_')))
        aux_prefix = aux_prefix.replace('-', '_')
        for dset_name in [aux_prefix + 'Indices', aux_prefix + 'Values']:
            if dset_name in h5_parent_group.keys():
                raise KeyError('Dataset named: ' + dset_name + ' already exists in group: '
                                                               '{}'.format(h5_parent_group.name))
        return aux_prefix

    if not isinstance(h5_parent_group, (h5py.Group, h5py.File)):
        raise TypeError('h5_parent_group should be a h5py.File or h5py.Group object')
    if not is_editable_h5(h5_parent_group):
        raise ValueError('The provided file is not editable')
    if verbose:
        print('h5 group and file OK')

    for arg, arg_name in zip([quantity, units, main_data_name],
                             ['quantity', 'units', 'main_data_name']):
        if not isinstance(arg, (str, unicode)):
            raise TypeError(arg_name + ' should be a string')
        if len(arg.strip()) <= 0:
            raise ValueError(arg_name + ' should not be an empty string')
    if verbose:
        print('quantity, units, main_data_name all OK')

    quantity = quantity.strip()
    units = units.strip()
    main_data_name = main_data_name.strip()
    if '-' in main_data_name:
        warn('main_data_name should not contain the "-" character. Reformatted name from:{} to '
             '{}'.format(main_data_name, main_data_name.replace('-', '_')))
    main_data_name = main_data_name.replace('-', '_')

    if isinstance(main_data, (list, tuple)):
        if not contains_integers(main_data, min_val=1):
            raise ValueError('main_data if specified as a shape should be a list / tuple of integers >= 1')
        if len(main_data) != 2:
            raise ValueError('main_data if specified as a shape should contain 2 numbers')
        if 'dtype' not in kwargs:
            raise ValueError('dtype must be included as a kwarg when creating an empty dataset')
        _ = validate_dtype(kwargs.get('dtype'))
        main_shape = main_data
        if verbose:
            print('Selected empty dataset creation. OK so far')
    elif isinstance(main_data, (np.ndarray, da.core.Array)):
        if main_data.ndim != 2:
            raise ValueError('main_data should be a 2D array')
        main_shape = main_data.shape
        if verbose:
            print('Provided numpy or Dask array for main_data OK so far')
    else:
        raise TypeError('main_data should either be a numpy array or a tuple / list with the shape of the data')

    if h5_pos_inds is not None and h5_pos_vals is not None:
        # The provided datasets override fresh building instructions.
        __validate_anc_h5_dsets(h5_pos_inds, h5_pos_vals, main_shape, is_spectroscopic=False)
        if verbose:
            print('Provided h5 position indices and values OK')
    else:
        aux_pos_prefix = __check_anc_before_creation(aux_pos_prefix, dim_type='pos')
        pos_dims = __validate_dimensions(pos_dims, dim_type='Position')
        # Check to make sure that the product of the position dimension sizes match with that of raw_data
        if main_shape[0] != np.product([len(x.values) for x in pos_dims]):
            raise ValueError('Position dimensions in main data do not match with product of values in pos_dims')
        if verbose:
            print('Passed all pre-tests for creating position datasets')
        h5_pos_inds, h5_pos_vals = write_ind_val_dsets(h5_parent_group, pos_dims, is_spectral=False, verbose=verbose,
                                                       base_name=aux_pos_prefix)
        if verbose:
            print('Created position datasets!')

    if h5_spec_inds is not None and h5_spec_vals is not None:
        # The provided datasets override fresh building instructions.
        __validate_anc_h5_dsets(h5_spec_inds, h5_spec_vals, main_shape, is_spectroscopic=True)
        if verbose:
            print('Provided h5 spectroscopic datasets were OK')
    else:
        aux_spec_prefix = __check_anc_before_creation(aux_spec_prefix, dim_type='spec')
        spec_dims = __validate_dimensions(spec_dims, dim_type='Spectroscopic')
        # Check to make sure that the product of the spectroscopic dimension sizes match with that of raw_data
        if main_shape[1] != np.product([len(x.values) for x in spec_dims]):
            raise ValueError('Spectroscopic dimensions in main data do not match with product of values in spec_dims')
        if verbose:
            print('Passed all pre-tests for creating spectroscopic datasets')
        h5_spec_inds, h5_spec_vals = write_ind_val_dsets(h5_parent_group, spec_dims, is_spectral=True, verbose=verbose,
                                                         base_name=aux_spec_prefix)
        if verbose:
            print('Created Spectroscopic datasets')

    if h5_parent_group.file.driver == 'mpio':
        if kwargs.pop('compression', None) is not None:
            warn('This HDF5 file has been opened wth the "mpio" communicator. '
                 'mpi4py does not allow creation of compressed datasets. Compression kwarg has been removed')

    if isinstance(main_data, np.ndarray):
        # Case 1 - simple small dataset
        h5_main = h5_parent_group.create_dataset(main_data_name, data=main_data, **kwargs)
        if verbose:
            print('Created main dataset with provided data')
    elif isinstance(main_data, da.core.Array):
        # Case 2 - Dask dataset
        # step 0 - get rid of any automated dtype specification:
        _ = kwargs.pop('dtype', None)
        # step 1 - create the empty dataset:
        h5_main = h5_parent_group.create_dataset(main_data_name, shape=main_data.shape, dtype=main_data.dtype,
                                                 **kwargs)
        if verbose:
            print('Created empty dataset: {} for writing Dask dataset: {}'.format(h5_main, main_data))
            print('Dask array will be written to HDF5 dataset: "{}" in file: "{}"'.format(h5_main.name,
                                                                                          h5_main.file.filename))
        # Step 2 - now ask Dask to dump data to disk
        da.to_hdf5(h5_main.file.filename, {h5_main.name: main_data})
        # main_data.to_hdf5(h5_main.file.filename, h5_main.name)  # Does not work with python 2 for some reason
    else:
        # Case 3 - large empty dataset
        h5_main = h5_parent_group.create_dataset(main_data_name, main_data, **kwargs)
        if verbose:
            print('Created empty dataset for Main')

    write_simple_attrs(h5_main, {'quantity': quantity, 'units': units})
    if verbose:
        print('Wrote quantity and units attributes to main dataset')

    if isinstance(main_dset_attrs, dict):
        write_simple_attrs(h5_main, main_dset_attrs)
        if verbose:
            print('Wrote provided attributes to main dataset')

    write_book_keeping_attrs(h5_main)

    # make it main
    link_as_main(h5_main, h5_pos_inds, h5_pos_vals, h5_spec_inds, h5_spec_vals)
    if verbose:
        print('Successfully linked datasets - dataset should be main now')

    from .usi_data import USIDataset
    return USIDataset(h5_main)


def copy_region_refs(h5_source, h5_target):
    """
    Check the input dataset for plot groups, copy them if they exist
    Also make references in the Spectroscopic Values and Indices tables

    Parameters
    ----------
    h5_source : HDF5 Dataset
            source dataset to copy references from
    h5_target : HDF5 Dataset
            target dataset the references from h5_source are copied to

    """
    '''
    Check both h5_source and h5_target to ensure that are Main
    '''
    are_main = all([check_if_main(h5_source), check_if_main(h5_target)])
    if not all([isinstance(h5_source, h5py.Dataset), isinstance(h5_target, h5py.Dataset)]):
        raise TypeError('Inputs to copyRegionRefs must be HDF5 Datasets or PycroDatasets.')

    if are_main:
        h5_source_inds = h5_source.file[h5_source.attrs['Spectroscopic_Indices']]

        h5_spec_inds = h5_target.file[h5_target.attrs['Spectroscopic_Indices']]
        h5_spec_vals = h5_target.file[h5_target.attrs['Spectroscopic_Values']]

    for key in h5_source.attrs.keys():
        if not isinstance(h5_source.attrs[key], h5py.RegionReference):
            continue

        if are_main:
            if h5_source_inds.shape[0] == h5_spec_inds.shape[0]:
                '''
                Spectroscopic dimensions are identical.
                Do direct copy.
                '''
                ref_inds = simple_region_ref_copy(h5_source, h5_target, key)

            else:
                '''
                Spectroscopic dimensions are different.
                Do the dimension reducing copy.
                '''
                ref_inds = copy_reg_ref_reduced_dim(h5_source, h5_target, h5_source_inds, h5_spec_inds, key)

            '''
            Create references for Spectroscopic Indices and Values
            Set the end-point of each hyperslab in the position dimension to the number of
            rows in the index array
            '''

            ref_inds[:, 1, 0][ref_inds[:, 1, 0] > h5_spec_inds.shape[0]] = h5_spec_inds.shape[0] - 1
            spec_inds_ref = create_region_reference(h5_spec_inds, ref_inds)
            h5_spec_inds.attrs[key] = spec_inds_ref
            spec_vals_ref = create_region_reference(h5_spec_vals, ref_inds)
            h5_spec_vals.attrs[key] = spec_vals_ref

        else:
            '''
            If not main datasets, then only simple copy can be used.
            '''
            simple_region_ref_copy(h5_source, h5_target, key)