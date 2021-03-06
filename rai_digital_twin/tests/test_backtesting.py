from rai_digital_twin.models.digital_twin_v1.run import post_processing
import pandas as pd
import numpy as np
from pytest import approx

from cadCAD_tools import easy_run
from cadCAD_tools.preparation import prepare_params
from rai_digital_twin import default_model
from rai_digital_twin.backtesting import *

def test_identical_backtesting():
    """
    Make sure that results when testing a df against itself is consistent.
    """
    # Run the model on default arguments
    sim_df = easy_run(default_model.initial_state,
                      prepare_params(default_model.parameters),
                      default_model.timestep_block,
                      30,
                      1,
                      assign_params=False)
    sim_df = post_processing(sim_df).fillna(0.0)
    test_df = sim_df.copy()

    cols = sim_df.dtypes
    numeric_inds = (cols == float) | (cols == int)
    numeric_cols = cols[numeric_inds].index

    # Assert that the loss when testing a dataframe against itself gives
    # null losses for each numerical column
    for col in numeric_cols:
        assert generic_column_loss(sim_df, test_df, col) == approx(0.0, 1e-5)

    # Assert consistency of the simulation metrics loss
    sim_metrics_losses = simulation_metrics_loss(sim_df, test_df)
    assert sim_metrics_losses.keys() == VALIDATION_METRICS.keys()
    for key, value in sim_metrics_losses.items():
        assert key in VALIDATION_METRICS.keys()
        assert type(value) == float

    # Assert that the simulation loss is consistent
    assert simulation_loss(sim_df, test_df) == approx(0.0, 1e-5)


def test_semi_identical_backtesting():
    """
    Make sure that testing a df against known semi identical dfs are consistent.
    """
    # Run the model on default arguments
    sim_df = easy_run(default_model.initial_state,
                      prepare_params(default_model.parameters),
                      default_model.timestep_block,
                      30,
                      1,
                      assign_params=False)
    sim_df = post_processing(sim_df)
    # Filter for only the numerical cols
    cols = sim_df.dtypes
    numeric_inds = (cols == float) | (cols == int)
    numeric_cols = cols[numeric_inds].index
    sim_df = sim_df.loc[:, numeric_cols]

    # Loss of a random dataframe must be higher than sim df against itself
    random_matrix = np.random.randint(0, 100, size=sim_df.shape)
    random_df = pd.DataFrame(random_matrix, columns=numeric_cols)
    assert simulation_loss(sim_df, random_df) > simulation_loss(sim_df, sim_df)

    # Test df is all simulation values multiplied by K
    test_df_1 = sim_df * 2
    test_df_2 = sim_df * 3

    # Loss must be larger than if it was the simulation df against itself
    assert simulation_loss(sim_df, test_df_1) > simulation_loss(sim_df, sim_df)
    assert simulation_loss(sim_df, test_df_2) > simulation_loss(sim_df, sim_df)

    # Loss must be commutative
    assert simulation_loss(
        sim_df, test_df_1) == simulation_loss(test_df_1, sim_df)
    assert simulation_loss(
        test_df_2, sim_df) == simulation_loss(sim_df, test_df_2)
    assert simulation_loss(test_df_2, test_df_1) == simulation_loss(
        test_df_1, test_df_2)
