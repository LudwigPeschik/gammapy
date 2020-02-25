# Licensed under a 3-clause BSD style license - see LICENSE.rst
import pytest
import numpy as np
from numpy.testing import assert_allclose
from gammapy.datasets import Datasets, FluxPointsDataset
from gammapy.modeling import Fit
from gammapy.modeling.models import PowerLawSpectralModel, SkyModel
from gammapy.spectrum import FluxPoints
from gammapy.utils.testing import mpl_plot_check, requires_data, requires_dependency


@pytest.fixture()
def fit(dataset):
    return Fit([dataset])


@pytest.fixture()
def dataset():
    path = "$GAMMAPY_DATA/tests/spectrum/flux_points/diff_flux_points.fits"
    data = FluxPoints.read(path)
    data.table["e_ref"] = data.e_ref.to("TeV")
    model = SkyModel(
        spectral_model=PowerLawSpectralModel(
            index=2.3, amplitude="2e-13 cm-2 s-1 TeV-1", reference="1 TeV"
        )
    )
    dataset = FluxPointsDataset(model, data)
    return dataset


@requires_data()
def test_flux_point_dataset_serialization(tmp_path):
    path = "$GAMMAPY_DATA/tests/spectrum/flux_points/diff_flux_points.fits"
    data = FluxPoints.read(path)
    data.table["e_ref"] = data.e_ref.to("TeV")
    spectral_model = PowerLawSpectralModel(
        index=2.3, amplitude="2e-13 cm-2 s-1 TeV-1", reference="1 TeV"
    )
    model = SkyModel(spectral_model=spectral_model, name="test_model")
    dataset = FluxPointsDataset(model, data, name="test_dataset")

    Datasets([dataset]).write(tmp_path, prefix="tmp")
    datasets = Datasets.read(
        tmp_path / "tmp_datasets.yaml", tmp_path / "tmp_models.yaml"
    )
    new_dataset = datasets[0]
    assert_allclose(new_dataset.data.table["dnde"], dataset.data.table["dnde"], 1e-4)
    if dataset.mask_fit is None:
        assert np.all(new_dataset.mask_fit == dataset.mask_safe)
    assert np.all(new_dataset.mask_safe == dataset.mask_safe)
    assert new_dataset.name == "test_dataset"


@requires_data()
def test_flux_point_dataset_str(dataset):
    assert "FluxPointsDataset" in str(dataset)


@requires_data()
class TestFluxPointFit:
    @requires_dependency("iminuit")
    def test_fit_pwl_minuit(self, fit):
        result = fit.run(backend="minuit")
        self.assert_result(result)

    @requires_dependency("sherpa")
    def test_fit_pwl_sherpa(self, fit):
        result = fit.optimize(backend="sherpa", method="simplex")
        self.assert_result(result)

    @staticmethod
    def assert_result(result):
        assert result.success
        assert_allclose(result.total_stat, 25.2059, rtol=1e-3)

        index = result.parameters["index"]
        assert_allclose(index.value, 2.216, rtol=1e-3)

        amplitude = result.parameters["amplitude"]
        assert_allclose(amplitude.value, 2.1616e-13, rtol=1e-3)

        reference = result.parameters["reference"]
        assert_allclose(reference.value, 1, rtol=1e-8)

    @staticmethod
    @requires_dependency("iminuit")
    def test_stat_profile(fit):

        result = fit.run(backend="minuit")

        profile = fit.stat_profile("amplitude", nvalues=3, bounds=1)

        ts_diff = profile["stat"] - result.total_stat
        assert_allclose(ts_diff, [110.1, 0, 110.1], rtol=1e-2, atol=1e-7)

        value = result.parameters["amplitude"].value
        err = result.parameters.error("amplitude")
        values = np.array([value - err, value, value + err])

        profile = fit.stat_profile("amplitude", values=values)

        ts_diff = profile["stat"] - result.total_stat
        assert_allclose(ts_diff, [110.1, 0, 110.1], rtol=1e-2, atol=1e-7)

    @staticmethod
    @requires_dependency("matplotlib")
    def test_fp_dataset_peek(fit):
        fp_dataset = fit.datasets[0]

        with mpl_plot_check():
            fp_dataset.peek(method="diff/model")
