import json
import pathlib
import re

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from colorama import Fore
from colorama import init
from prettytable import PrettyTable

from passivbot.utils.funcs.njit import round_dynamic
from passivbot.utils.funcs.njit import round_up
from passivbot.utils.funcs.pure import denumpyize
from passivbot.utils.procedures import dump_live_config


def dump_plots(
    session_plots_dirpath: pathlib.Path,
    result: dict,
    fdf: pd.DataFrame,
    sdf: pd.DataFrame,
    df: pd.DataFrame,
):
    init(autoreset=True)
    plt.rcParams["figure.figsize"] = [29, 18]
    pd.set_option("precision", 10)

    table = PrettyTable(["Metric", "Value"])
    table.align["Metric"] = "l"
    table.align["Value"] = "l"
    table.title = "Summary"

    table.add_row(["Exchange", result["exchange"] if "exchange" in result else "unknown"])
    table.add_row(["Market type", result["market_type"] if "market_type" in result else "unknown"])
    table.add_row(["Symbol", result["symbol"] if "symbol" in result else "unknown"])
    table.add_row(["No. days", round_dynamic(result["result"]["n_days"], 6)])
    table.add_row(["Starting balance", round_dynamic(result["result"]["starting_balance"], 6)])
    profit_color = (
        Fore.RED
        if result["result"]["final_balance"] < result["result"]["starting_balance"]
        else Fore.RESET
    )
    table.add_row(
        [
            "Final balance",
            f"{profit_color}{round_dynamic(result['result']['final_balance'], 6)}{Fore.RESET}",
        ]
    )
    table.add_row(
        [
            "Final equity",
            f"{profit_color}{round_dynamic(result['result']['final_equity'], 6)}{Fore.RESET}",
        ]
    )
    table.add_row(
        [
            "Net PNL + fees",
            f"{profit_color}{round_dynamic(result['result']['net_pnl_plus_fees'], 6)}{Fore.RESET}",
        ]
    )
    table.add_row(
        [
            "Total gain percentage",
            f"{profit_color}{round_dynamic(result['result']['gain'] * 100, 4)}%{Fore.RESET}",
        ]
    )
    table.add_row(
        [
            "Average daily gain percentage",
            f"{profit_color}{round_dynamic((result['result']['average_daily_gain']) * 100, 3)}%{Fore.RESET}",
        ]
    )
    table.add_row(
        [
            "Adjusted daily gain",
            f"{profit_color}{round_dynamic(result['result']['adjusted_daily_gain'], 6)}{Fore.RESET}",
        ]
    )
    bankruptcy_color = (
        Fore.RED
        if result["result"]["closest_bkr"] < 0.4
        else Fore.YELLOW
        if result["result"]["closest_bkr"] < 0.8
        else Fore.RESET
    )
    table.add_row(
        [
            "Closest bankruptcy percentage",
            f"{bankruptcy_color}{round_dynamic(result['result']['closest_bkr'] * 100, 4)}%{Fore.RESET}",
        ]
    )
    table.add_row([" ", " "])
    table.add_row(
        [
            "Profit sum",
            f"{profit_color}{round_dynamic(result['result']['profit_sum'], 6)}{Fore.RESET}",
        ]
    )
    table.add_row(
        ["Loss sum", f"{Fore.RED}{round_dynamic(result['result']['loss_sum'], 6)}{Fore.RESET}"]
    )
    table.add_row(["Fee sum", round_dynamic(result["result"]["fee_sum"], 6)])
    table.add_row(
        ["Lowest equity/balance ratio", round_dynamic(result["result"]["eqbal_ratio_min"], 6)]
    )
    table.add_row(["Biggest psize", round_dynamic(result["result"]["biggest_psize"], 6)])
    table.add_row(
        [
            "Price action distance mean long",
            round_dynamic(result["result"]["pa_distance_mean_long"], 6),
        ]
    )
    table.add_row(
        [
            "Price action distance median long",
            round_dynamic(result["result"]["pa_distance_median_long"], 6),
        ]
    )
    table.add_row(
        [
            "Price action distance max long",
            round_dynamic(result["result"]["pa_distance_max_long"], 6),
        ]
    )
    table.add_row(
        ["Average n fills per day", round_dynamic(result["result"]["avg_fills_per_day"], 6)]
    )
    table.add_row([" ", " "])
    table.add_row(["No. fills", round_dynamic(result["result"]["n_fills"], 6)])
    table.add_row(["No. entries", round_dynamic(result["result"]["n_entries"], 6)])
    table.add_row(["No. closes", round_dynamic(result["result"]["n_closes"], 6)])
    table.add_row(["No. initial entries", round_dynamic(result["result"]["n_ientries"], 6)])
    table.add_row(["No. reentries", round_dynamic(result["result"]["n_rentries"], 6)])
    table.add_row([" ", " "])
    table.add_row(
        ["Mean hours between fills", round_dynamic(result["result"]["hrs_stuck_avg_long"], 6)]
    )
    table.add_row(
        ["Max hours no fills (same side)", round_dynamic(result["result"]["hrs_stuck_max_long"], 6)]
    )
    table.add_row(["Max hours no fills", round_dynamic(result["result"]["hrs_stuck_max_long"], 6)])

    longs = fdf[fdf.type.str.contains("long")]
    shorts = fdf[fdf.type.str.contains("short")]
    if result["long"]["enabled"]:
        table.add_row([" ", " "])
        table.add_row(["Long", result["long"]["enabled"]])
        table.add_row(["No. inital entries", len(longs[longs.type.str.contains("ientry")])])
        table.add_row(["No. reentries", len(longs[longs.type.str.contains("rentry")])])
        table.add_row(["No. normal closes", len(longs[longs.type.str.contains("nclose")])])
        table.add_row(
            ["Mean hours stuck (long)", round_dynamic(result["result"]["hrs_stuck_avg_long"], 6)]
        )
        table.add_row(
            ["Max hours stuck (long)", round_dynamic(result["result"]["hrs_stuck_max_long"], 6)]
        )
        profit_color = Fore.RED if longs.pnl.sum() < 0 else Fore.RESET
        table.add_row(["PNL sum", f"{profit_color}{longs.pnl.sum()}{Fore.RESET}"])

    if result["short"]["enabled"]:
        table.add_row([" ", " "])
        table.add_row(["Short", result["short"]["enabled"]])
        table.add_row(["No. initial entries", len(shorts[shorts.type.str.contains("ientry")])])
        table.add_row(["No. reentries", len(shorts[shorts.type.str.contains("rentry")])])
        table.add_row(["No. normal closes", len(shorts[shorts.type.str.contains("nclose")])])
        table.add_row(
            [
                "Mean hours between fills (short)",
                round_dynamic(result["result"]["mean_hrs_between_fills_short"], 6),
            ]
        )
        table.add_row(
            [
                "Max hours no fills (short)",
                round_dynamic(result["result"]["max_hrs_no_fills_short"], 6),
            ]
        )
        profit_color = Fore.RED if shorts.pnl.sum() < 0 else Fore.RESET
        table.add_row(["PNL sum", f"{profit_color}{shorts.pnl.sum()}{Fore.RESET}"])

    dump_live_config(result, session_plots_dirpath / "live_config.json")
    json.dump(denumpyize(result), session_plots_dirpath.joinpath("result.json").open("w"), indent=4)

    print("writing backtest_result.txt...\n")
    with session_plots_dirpath.joinpath("backtest_result.txt").open("w") as f:
        output = table.get_string(border=True, padding_width=1)
        print(output)
        f.write(re.sub("\033\\[([0-9]+)(;[0-9]+)*m", "", output))

    print("\nplotting balance and equity...")
    plt.clf()
    sdf.balance.plot()
    sdf.equity.plot()
    plt.savefig(session_plots_dirpath / "balance_and_equity_sampled.png")

    plt.clf()
    longs.pnl.cumsum().plot()
    plt.savefig(session_plots_dirpath / "pnl_cumsum_long.png")

    plt.clf()
    shorts.pnl.cumsum().plot()
    plt.savefig(session_plots_dirpath / "pnl_cumsum_short.png")

    adg = (sdf.equity / sdf.equity.iloc[0]) ** (
        1 / ((sdf.timestamp - sdf.timestamp.iloc[0]) / (1000 * 60 * 60 * 24))
    )
    plt.clf()
    adg.plot()
    plt.savefig(session_plots_dirpath / "adg.png")

    print("plotting backtest whole and in chunks...")
    n_parts = max(3, int(round_up(result["n_days"] / 14, 1.0)))
    for z in range(n_parts):
        start_ = z / n_parts
        end_ = (z + 1) / n_parts
        print(f"{z} of {n_parts} {start_ * 100:.2f}% to {end_ * 100:.2f}%")
        fig = plot_fills(df, fdf.iloc[int(len(fdf) * start_) : int(len(fdf) * end_)], bkr_thr=0.1)
        if fig is not None:
            fig.savefig(session_plots_dirpath / f"backtest_{z + 1}of{n_parts}.png")
        else:
            print("no fills...")
    fig = plot_fills(df, fdf, bkr_thr=0.1, plot_whole_df=True)
    fig.savefig(session_plots_dirpath / "whole_backtest.png")

    print("plotting pos sizes...")
    plt.clf()
    longs.psize.plot()
    shorts.psize.plot()
    plt.savefig(session_plots_dirpath / "psizes_plot.png")


def plot_fills(df, fdf_, side: int = 0, bkr_thr=0.1, plot_whole_df: bool = False):
    if fdf_.empty:
        return
    plt.clf()
    fdf = fdf_.set_index("timestamp")
    dfc = df  # .iloc[::max(1, int(len(df) * 0.00001))]
    if dfc.index.name != "timestamp":
        dfc = dfc.set_index("timestamp")
    if not plot_whole_df:
        dfc = dfc[(dfc.index > fdf.index[0]) & (dfc.index < fdf.index[-1])]
        dfc = dfc.loc[fdf.index[0] : fdf.index[-1]]
    dfc.price.plot(style="y-")

    if side >= 0:
        longs = fdf[fdf.type.str.contains("long")]
        lientry = longs[longs.type.str.contains("ientry")]
        lrentry = longs[longs.type.str.contains("rentry")]
        lnclose = longs[longs.type.str.contains("nclose")]
        lsclose = longs[longs.type.str.contains("sclose")]
        ldca = longs[longs.type.str.contains("secondary")]
        lientry.price.plot(style="b.")
        lrentry.price.plot(style="b.")
        lnclose.price.plot(style="r.")
        lsclose.price.plot(style="rx")
        ldca.price.plot(style="go")

        longs.where(longs.pprice != 0.0).pprice.fillna(method="ffill").plot(style="b--")
    if side <= 0:
        shorts = fdf[fdf.type.str.contains("short")]
        sientry = shorts[shorts.type.str.contains("ientry")]
        srentry = shorts[shorts.type.str.contains("rentry")]
        snclose = shorts[shorts.type.str.contains("nclose")]
        ssclose = shorts[shorts.type.str.contains("sclose")]
        sdca = shorts[shorts.type.str.contains("secondary")]
        sientry.price.plot(style="r.")
        srentry.price.plot(style="r.")
        snclose.price.plot(style="b.")
        ssclose.price.plot(style="bx")
        sdca.price.plot(style="go")
        shorts.where(shorts.pprice != 0.0).pprice.fillna(method="ffill").plot(style="r--")

    if "bkr_price" in fdf.columns:
        fdf.bkr_price.where(fdf.bkr_diff < bkr_thr, np.nan).plot(style="k--")
    return plt
