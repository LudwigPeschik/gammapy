# Licensed under a 3-clause BSD style license - see LICENSE.rst
from __future__ import absolute_import, division, print_function, unicode_literals
import pytest
import numpy as np
from numpy.testing import assert_allclose
import astropy.units as u
from astropy.coordinates import SkyCoord
from regions import CircleSkyRegion
from ...utils.testing import requires_data, requires_dependency
from ...irf import EffectiveAreaTable2D, EnergyDependentMultiGaussPSF
from ...irf.energy_dispersion import EnergyDispersion
from ...maps import MapAxis, WcsGeom, WcsNDMap, Map
from ...image.models import SkyGaussian
from ...spectrum.models import PowerLaw
from ..models import SkyModel
from .. import MapEvaluator, MapFit, make_map_exposure_true_energy, PSFKernel


def geom(ebounds):
    axis = MapAxis.from_edges(ebounds, name="energy", unit=u.TeV)
    return WcsGeom.create(
        skydir=(0, 0), binsz=0.02, width=(2, 2), coordsys="GAL", axes=[axis]
    )


def geom_etrue(ebounds_true):
    axis = MapAxis.from_edges(ebounds_true, name="energy", unit=u.TeV)
    return WcsGeom.create(
        skydir=(0, 0), binsz=0.02, width=(2, 2), coordsys="GAL", axes=[axis]
    )


def exposure(geom_etrue):
    filename = "$GAMMAPY_EXTRA/datasets/cta-1dc/caldb/data/cta/1dc/bcf/South_z20_50h/irf_file.fits"
    aeff = EffectiveAreaTable2D.read(filename, hdu="EFFECTIVE AREA")

    exposure_map = make_map_exposure_true_energy(
        pointing=SkyCoord(1, 0.5, unit="deg", frame="galactic"),
        livetime="1 hour",
        aeff=aeff,
        geom=geom_etrue,
    )
    return exposure_map


def background(geom):
    m = Map.from_geom(geom)
    m.quantity = np.ones(m.data.shape) * 1e-5
    return m


def edisp(geom, geom_etrue):
    e_true = geom_etrue.get_axis_by_name("energy").edges
    e_reco = geom.get_axis_by_name("energy").edges
    return EnergyDispersion.from_diagonal_response(e_true=e_true, e_reco=e_reco)


def psf(geom_etrue):
    filename = "$GAMMAPY_EXTRA/datasets/cta-1dc/caldb/data/cta/1dc/bcf/South_z20_50h/irf_file.fits"
    psf = EnergyDependentMultiGaussPSF.read(filename, hdu="POINT SPREAD FUNCTION")

    table_psf = psf.to_energy_dependent_table_psf(theta=0.5 * u.deg)
    psf_kernel = PSFKernel.from_table_psf(table_psf, geom_etrue, max_radius=0.5 * u.deg)
    return psf_kernel


@pytest.fixture
def sky_model():
    spatial_model = SkyGaussian(lon_0="0.2 deg", lat_0="0.1 deg", sigma="0.2 deg")
    spectral_model = PowerLaw(
        index=3, amplitude="1e-11 cm-2 s-1 TeV-1", reference="1 TeV"
    )
    return SkyModel(spatial_model=spatial_model, spectral_model=spectral_model)


@pytest.fixture
def sky_model_fit(sky_model):
    spatial_model = SkyGaussian(lon_0="0.5 deg", lat_0="0.5 deg", sigma="0.2 deg")
    spectral_model = PowerLaw(
        index=2, amplitude="2e-11 cm-2 s-1 TeV-1", reference="1 TeV"
    )
    return SkyModel(spatial_model=spatial_model, spectral_model=spectral_model)


def mask(geom, sky_model):
    p = sky_model.spatial_model.parameters
    center = SkyCoord(p["lon_0"].value, p["lat_0"].value, frame="galactic", unit="deg")
    circle = CircleSkyRegion(center=center, radius=1 * u.deg)
    data = geom.region_mask([circle])
    return WcsNDMap(geom=geom, data=data)


def counts(sky_model, exposure, background, psf, edisp):
    evaluator = MapEvaluator(
        model=sky_model, exposure=exposure, background=background, psf=psf, edisp=edisp
    )
    npred = evaluator.compute_npred()
    return WcsNDMap(background.geom, npred)


@requires_dependency("iminuit")
@requires_data("gammapy-extra")
def test_cube_fit(sky_model, sky_model_fit):
    ebounds = np.logspace(-1.0, 1.0, 3)
    ebounds_true = np.logspace(-1.0, 1.0, 4)
    geom_r = geom(ebounds)
    geom_t = geom_etrue(ebounds_true)

    background_map = background(geom_r)
    psf_map = psf(geom_t)
    edisp_map = edisp(geom_r, geom_t)
    exposure_map = exposure(geom_t)
    counts_map = counts(sky_model, exposure_map, background_map, psf_map, edisp_map)
    mask_map = mask(geom_r, sky_model)

    sky_model_fit.parameters["sigma"].frozen = True

    fit = MapFit(
        model=sky_model_fit,
        counts=counts_map,
        exposure=exposure_map,
        background=background_map,
        mask=mask_map,
        psf=psf_map,
        edisp=edisp_map,
    )
    result = fit.run()

    assert sky_model is not fit._model
    assert sky_model is not result.model
    assert result.success
    assert "minuit" in repr(result)

    stat_expected = 5417.350078
    assert_allclose(result.total_stat, stat_expected, rtol=1e-2)

    pars = result.model.parameters
    assert_allclose(pars["lon_0"].value, 0.2, rtol=1e-2)
    assert_allclose(pars.error("lon_0"), 0.004177, rtol=1e-2)

    assert_allclose(pars["index"].value, 3, rtol=1e-2)
    assert_allclose(pars.error("index"), 0.033947, rtol=1e-2)

    assert_allclose(pars["amplitude"].value, 1e-11, rtol=1e-2)
    assert_allclose(pars.error("amplitude"), 4.03049e-13, rtol=1e-2)

    # asserts on sub-covariance
    pars_spatial = result.model.spatial_model.parameters
    assert_allclose(pars_spatial.error("lon_0"), 0.004177, rtol=1e-2)

    pars_spectral = result.model.spectral_model.parameters
    assert_allclose(pars_spectral.error("index"), 0.033947, rtol=1e-2)


@requires_dependency("iminuit")
@requires_data("gammapy-extra")
def test_cube_fit_onebin(sky_model, sky_model_fit):
    ebounds = np.logspace(-1.0, 1.0, 2)
    geom_r = geom(ebounds)

    background_map = background(geom_r)
    psf_map = psf(geom_r)
    edisp_map = edisp(geom_r, geom_r)
    exposure_map = exposure(geom_r)
    counts_map = counts(sky_model, exposure_map, background_map, psf_map, edisp_map)
    mask_map = mask(geom_r, sky_model)

    sky_model_fit.parameters["index"].value = 3.0
    sky_model_fit.parameters["index"].frozen = True
    sky_model_fit.parameters["sigma"].value = 0.3

    fit = MapFit(
        model=sky_model_fit,
        counts=counts_map,
        exposure=exposure_map,
        background=background_map,
        mask=mask_map,
        psf=psf_map,
        edisp=edisp_map,
    )
    result = fit.run()

    assert result.success

    stat_expected = 697.068035
    assert_allclose(result.total_stat, stat_expected, rtol=1e-1)

    pars = result.model.parameters
    assert_allclose(pars["lon_0"].value, 0.2, rtol=1e-1)
    assert_allclose(pars.error("lon_0"), 0.02, rtol=1e-1)

    assert_allclose(pars["sigma"].value, 0.2, rtol=1e-1)
    assert_allclose(pars.error("sigma"), 0.011, rtol=1e-1)

    assert_allclose(pars["amplitude"].value, 1e-11, rtol=1e-1)
    assert_allclose(pars.error("amplitude"), 1.07049e-12, rtol=1e-1)
