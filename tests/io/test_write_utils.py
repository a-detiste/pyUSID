# -*- coding: utf-8 -*-
"""
Created on Tue Nov  3 15:07:16 2017

@author: Suhas Somnath
"""
from __future__ import division, print_function, unicode_literals, absolute_import
import unittest
import sys
import numpy as np

sys.path.append("../../pyUSID/")
from pyUSID.io import anc_build_utils

if sys.version_info.major == 3:
    unicode = str


class TestMakeIndicesMatrix(unittest.TestCase):

    def test_dim_w_val_1(self):
        with self.assertRaises(ValueError):
            _ = anc_build_utils.make_indices_matrix([1, 2, 3])

    def test_just_size_of_one_dim(self):
        expected = np.expand_dims(np.arange(4), axis=0)
        ret_val = anc_build_utils.make_indices_matrix(4, is_position=False)
        self.assertTrue(np.allclose(expected, ret_val))

    def test_empty_list(self):
        with self.assertRaises(ValueError):
            _ = anc_build_utils.make_indices_matrix([])

    def test_single_value_dimension_int_input(self):
        expected = np.expand_dims(np.arange(1), axis=0)
        ret_val = anc_build_utils.make_indices_matrix(1, is_position=False)
        self.assertTrue(np.allclose(expected, ret_val))

    def test_single_value_dimension_list_input(self):
        expected = np.expand_dims(np.arange(1), axis=0)
        ret_val = anc_build_utils.make_indices_matrix([1], is_position=False)
        self.assertTrue(np.allclose(expected, ret_val))

    def test_non_int_dim_sizes(self):
        with self.assertRaises(ValueError):
            _ = anc_build_utils.make_indices_matrix([1.233, 2.4, 3])

    def test_weird_inputs(self):
        with self.assertRaises(ValueError):
            _ = anc_build_utils.make_indices_matrix([2, 'hello', 3])

    def test_matrix_1_dims(self):
        expected = np.expand_dims(np.arange(4), axis=0)
        ret_val = anc_build_utils.make_indices_matrix([4], is_position=False)
        self.assertTrue(np.allclose(expected, ret_val))
        ret_val = anc_build_utils.make_indices_matrix([4], is_position=True)
        self.assertTrue(np.allclose(expected.T, ret_val))

    def test_2_dims(self):
        expected = np.vstack((np.tile(np.arange(2), 3),
                              np.repeat(np.arange(3), 2)))
        ret_val = anc_build_utils.make_indices_matrix([2, 3], is_position=False)
        self.assertTrue(np.allclose(expected, ret_val))
        ret_val = anc_build_utils.make_indices_matrix([2, 3], is_position=True)
        self.assertTrue(np.allclose(expected.T, ret_val))

    def test_3_dims(self):
        expected = np.vstack((np.tile(np.arange(2), 3 * 4),
                              np.tile(np.repeat(np.arange(3), 2), 4),
                              np.repeat(np.arange(4), 6)))
        ret_val = anc_build_utils.make_indices_matrix([2, 3, 4], is_position=False)
        self.assertTrue(np.allclose(expected, ret_val))
        ret_val = anc_build_utils.make_indices_matrix([2, 3, 4], is_position=True)
        self.assertTrue(np.allclose(expected.T, ret_val))


class TestGetAuxDsetSlicing(unittest.TestCase):

    def test_legal_single_dim(self):
        ret_val = anc_build_utils.get_aux_dset_slicing(['X'], is_spectroscopic=True)
        expected = {'X': (slice(0, 1), slice(None))}
        self.assertEqual(ret_val, expected)

        ret_val = anc_build_utils.get_aux_dset_slicing(['X'], is_spectroscopic=False)
        expected = {'X': (slice(None), slice(0, 1))}
        self.assertEqual(ret_val, expected)

    def test_legal_multi_dim(self):
        ret_val = anc_build_utils.get_aux_dset_slicing(['X', 'Y'], is_spectroscopic=True)
        expected = {'X': (slice(0, 1), slice(None)), 'Y': (slice(1, 2), slice(None))}
        self.assertEqual(ret_val, expected)

        ret_val = anc_build_utils.get_aux_dset_slicing(['X', 'Y'], is_spectroscopic=False)
        expected = {'X': (slice(None), slice(0, 1)), 'Y': (slice(None), slice(1, 2))}
        self.assertEqual(ret_val, expected)

    def test_odd_input(self):
        with self.assertRaises(TypeError):
            _ = anc_build_utils.get_aux_dset_slicing([1, 'Y'], is_spectroscopic=True)
        with self.assertRaises(ValueError):
            _ = anc_build_utils.get_aux_dset_slicing([], is_spectroscopic=True)


class TestBuildIndValMatrices(unittest.TestCase):

    def test_empty(self):
        inds, vals = anc_build_utils.build_ind_val_matrices([[0]], is_spectral=True)
        self.assertTrue(np.allclose(inds, anc_build_utils.INDICES_DTYPE(np.expand_dims(np.arange(1), 0))))
        self.assertTrue(np.allclose(vals, anc_build_utils.VALUES_DTYPE(np.expand_dims(np.arange(1), 0))))

    def test_1D(self):
        sine_val = np.sin(np.linspace(0, 2*np.pi, 128))
        inds, vals = anc_build_utils.build_ind_val_matrices([sine_val], is_spectral=True)
        self.assertTrue(np.allclose(inds, anc_build_utils.INDICES_DTYPE(np.expand_dims(np.arange(len(sine_val)), axis=0))))
        self.assertTrue(np.allclose(vals, anc_build_utils.VALUES_DTYPE(np.expand_dims(sine_val, axis=0))))

    def test_1D_pos(self):
        sine_val = np.sin(np.linspace(0, 2 * np.pi, 128))
        inds, vals = anc_build_utils.build_ind_val_matrices([sine_val], is_spectral=False)
        self.assertTrue(np.allclose(inds, anc_build_utils.INDICES_DTYPE(np.expand_dims(np.arange(len(sine_val)), axis=1))))
        self.assertTrue(np.allclose(vals, anc_build_utils.VALUES_DTYPE(np.expand_dims(sine_val, axis=1))))

    def test_3D(self):
        max_v = 4
        half_pts = 8
        bi_triang = np.roll(np.hstack((np.linspace(-max_v, max_v, half_pts, endpoint=False),
                                       np.linspace(max_v, -max_v, half_pts, endpoint=False))), -half_pts // 2)
        cycles = [0, 1, 2]
        fields = [0, 1]
        exp_vals = np.vstack((np.tile(bi_triang, 6), np.tile(np.repeat(fields, 2 * half_pts), 3),
                              np.repeat(cycles, 2 * 2 * half_pts)))
        exp_inds = np.vstack((np.tile(np.arange(2 * half_pts), 6), np.tile(np.repeat(fields, 2 * half_pts), 3),
                              np.repeat(cycles, 2 * 2 * half_pts)))
        inds, vals = anc_build_utils.build_ind_val_matrices([bi_triang, fields, cycles])
        self.assertTrue(np.allclose(exp_inds, inds))
        self.assertTrue(np.allclose(exp_vals, vals))

    def test_invalid_inputs(self):
        with self.assertRaises(TypeError):
            _ = anc_build_utils.build_ind_val_matrices("not a list of arrays")

        with self.assertRaises(ValueError):
            _ = anc_build_utils.build_ind_val_matrices([[0, 1], np.random.randint(0, high=5, size=(3, 4))])


class TestCreateSpecIndsFromVals(unittest.TestCase):

    def test_legal(self):
        max_v = 4
        half_pts = 8
        bi_triang = np.roll(np.hstack((np.linspace(-max_v, max_v, half_pts, endpoint=False),
                                       np.linspace(max_v, -max_v, half_pts, endpoint=False))), -half_pts // 2)
        cycles = [0, 1, 2]
        fields = [0, 1]
        exp_vals = np.vstack((np.tile(bi_triang, 6), np.tile(np.repeat(fields, 2 * half_pts), 3),
                              np.repeat(cycles, 2 * 2 * half_pts)))
        exp_inds = np.vstack((np.tile(np.arange(2 * half_pts), 6), np.tile(np.repeat(fields, 2 * half_pts), 3),
                              np.repeat(cycles, 2 * 2 * half_pts)))

        inds = anc_build_utils.create_spec_inds_from_vals(exp_vals)
        self.assertTrue(np.allclose(inds, exp_inds))

    def test_invalid_inputs(self):
        with self.assertRaises(TypeError):
            _ = anc_build_utils.create_spec_inds_from_vals([[0, 1, 0, 1],
                                                            [0, 0, 1, 1]])

        with self.assertRaises(ValueError):
            _ = anc_build_utils.create_spec_inds_from_vals(np.random.rand(2, 3, 4))


class TestCalcChunks(unittest.TestCase):

    def test_no_unit_chunk(self):
        dimensions = (16384, 16384 * 4)
        dtype_bytesize = 4
        unit_chunks = None
        ret_val = anc_build_utils.calc_chunks(dimensions, dtype_bytesize, unit_chunks=unit_chunks)
        self.assertTrue(np.allclose(ret_val, (26, 100)))

    def test_unit_chunk(self):
        dimensions = (16384, 16384 * 4)
        dtype_bytesize = 4
        unit_chunks = (3, 7)
        ret_val = anc_build_utils.calc_chunks(dimensions, dtype_bytesize, unit_chunks=unit_chunks)
        self.assertTrue(np.allclose(ret_val, (27, 98)))

    def test_no_unit_chunk_max_mem(self):
        dimensions = (16384, 16384 * 4)
        dtype_bytesize = 4
        unit_chunks = None
        max_mem = 50000
        ret_val = anc_build_utils.calc_chunks(dimensions, dtype_bytesize, unit_chunks=unit_chunks, max_chunk_mem=max_mem)
        self.assertTrue(np.allclose(ret_val, (56, 224)))

    def test_unit_chunk_max_mem(self):
        dimensions = (16384, 16384 * 4)
        dtype_bytesize = 4
        unit_chunks = (3, 7)
        max_mem = 50000
        ret_val = anc_build_utils.calc_chunks(dimensions, dtype_bytesize, unit_chunks=unit_chunks, max_chunk_mem=max_mem)
        self.assertTrue(np.allclose(ret_val, (57, 224)))

    def test_unit_not_iterable(self):
        dimensions = (16384, 16384 * 4)
        dtype_bytesize = 4
        unit_chunks = 4

        with self.assertRaises(TypeError):
            _ = anc_build_utils.calc_chunks(dimensions, dtype_bytesize, unit_chunks=unit_chunks)

    def test_shape_mismatch(self):
        dimensions = (16384, 16384 * 4)
        dtype_bytesize = 4
        unit_chunks = (1, 5, 9)

        with self.assertRaises(ValueError):
            _ = anc_build_utils.calc_chunks(dimensions, dtype_bytesize, unit_chunks=unit_chunks)

    def test_invalid_types(self):
        with self.assertRaises(TypeError):
            _ = anc_build_utils.calc_chunks("Fdfd", 14)

        with self.assertRaises(TypeError):
            _ = anc_build_utils.calc_chunks((16384, 16384 * 4), 2.124)


if __name__ == '__main__':
    unittest.main()
