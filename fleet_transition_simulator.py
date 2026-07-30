
def clone(value):
    """Copy nested dictionaries and lists without external libraries."""
    if isinstance(value, dict):
        return {key: clone(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clone(item) for item in value]
    return value


def get_path(data, path):
    """Read a value from a nested dictionary path."""
    for key in path:
        data = data[key]
    return data


def set_path(data, path, value):
    """Write a value to a nested dictionary path."""
    for key in path[:-1]:
        data = data[key]
    data[path[-1]] = value


def factor(curve, index):
    """Return the linear factor configured for one year."""
    return float(curve["factor_start"]) + float(curve["factor_step"]) * index


def value(curve, index):
    """Return a base value multiplied by its yearly factor."""
    return float(curve["base"]) * factor(curve, index)


def model_years(model):
    """Build all model years required by the scenario."""
    first = int(model["model_first_year"])
    last = int(model["scenario"]["start_year"]) + int(model["scenario"]["years"]) - 1
    return list(range(first, last + 1))


def scenario_years(model):
    """Build the planning-horizon year vector."""
    start = int(model["scenario"]["start_year"])
    return [start + i for i in range(int(model["scenario"]["years"]))]


def annual_series(model, technology):
    """Calculate annual capex, energy and toll values for one technology."""
    common, tech = model["common"], model["technology"][technology]
    years = model_years(model)
    km = float(common["daily_km"]["base"])
    days = int(common["operating_days_per_year"])
    replacement = int(common["replacement_frequency_years"])
    lifecycle_km = km * days * replacement
    result = {
        "Technology": technology,
        "Year": years,
        "Lifecycle KM": lifecycle_km,
        "Cost per km equipment": [],
        "Daily cost equipment": [],
        "Daily energy quantity": [],
        "Net energy unit cost": [],
        "Daily cost toll": [],
    }

    for i in range(len(years)):
        capex_per_km = (value(tech["truck_cost"], i) + value(tech["truck_incentive"], i)) / lifecycle_km
        km_factor = factor(common["daily_km"], i)

        # The multiplication by the efficiency factor reproduces the workbook formula.
        quantity = km * km_factor / float(tech["efficiency"]["base"]) * factor(tech["efficiency"], i)
        energy_unit_cost = value(tech["energy_price"], i) + value(tech["energy_incentive"], i)
        toll = (value(tech["toll_rate"], i) + value(tech["toll_incentive"], i)) * km * km_factor

        result["Cost per km equipment"].append(capex_per_km)
        result["Daily cost equipment"].append(capex_per_km * km)
        result["Daily energy quantity"].append(quantity)
        result["Net energy unit cost"].append(energy_unit_cost)
        result["Daily cost toll"].append(toll)
    return result


def series_value(series, metric, year):
    """Read a series value by year."""
    index = int(year) - int(series["Year"][0])
    if index < 0 or index >= len(series["Year"]):
        raise ValueError("Year not available: " + str(year))
    return float(series[metric][index])


def active_model_year(year, initial_year, replacement_frequency):
    """Return the truck model year active in a scenario year."""
    replacements = max(0, (int(year) - int(initial_year)) // int(replacement_frequency))
    return int(initial_year) + replacements * int(replacement_frequency)


def fleet_scenario(model, technology, series=None):
    """Calculate the homogeneous Diesel or Electric fleet matrix."""
    series = annual_series(model, technology) if series is None else series
    years = scenario_years(model)
    common = model["common"]
    replacement = int(common["replacement_frequency_years"])
    days = int(common["operating_days_per_year"])
    quantity_key = model["technology"][technology]["labels"]["quantity"]
    trucks = {}

    for index, initial_year in enumerate(model["fleet_initial_model_years"]):
        truck = {key: [] for key in [
            "Year model", "Technology", "Truck cost", quantity_key,
            "Cost fuel", "Toll cost", "Daily cost", "Yearly cost",
        ]}
        for year in years:
            truck_year = active_model_year(year, initial_year, replacement)
            capex = series_value(series, "Daily cost equipment", truck_year)
            quantity = series_value(series, "Daily energy quantity", truck_year)
            fuel = quantity * series_value(series, "Net energy unit cost", year)
            toll = series_value(series, "Daily cost toll", year)
            daily = capex + fuel + toll

            values = [truck_year, technology, capex, quantity, fuel, toll, daily, daily * days]
            for key, item in zip(truck, values):
                truck[key].append(item)

        truck["Total"] = sum(truck["Yearly cost"])
        trucks["Truck " + str(index + 1)] = truck

    return {
        "Technology": technology,
        "Scenario years": years,
        "Trucks": trucks,
        "Total": sum(truck["Total"] for truck in trucks.values()),
    }


def normalize_plan(plan, diesel):
    """Convert a legacy model-year threshold into truck calendar years."""
    names = list(diesel["Trucks"])
    if isinstance(plan, dict):
        return {name: plan.get(name) for name in names}

    normalized = {}
    for name in names:
        normalized[name] = None
        for i, truck_year in enumerate(diesel["Trucks"][name]["Year model"]):
            if int(truck_year) >= int(plan):
                normalized[name] = diesel["Scenario years"][i]
                break
    return normalized


def transition_scenario(diesel, electric, plan):
    """Combine Diesel and Electric costs using a truck-level transition plan."""
    plan = normalize_plan(plan, diesel)
    trucks = {}
    for name in diesel["Trucks"]:
        truck = {key: [] for key in [
            "Year model", "Technology", "Truck cost", "Liters/kWh",
            "Cost fuel", "Toll cost", "Daily cost", "Yearly cost",
        ]}
        for i, year in enumerate(diesel["Scenario years"]):
            use_electric = plan[name] is not None and year >= int(plan[name])
            source = electric["Trucks"][name] if use_electric else diesel["Trucks"][name]
            technology = "ELECTRIC" if use_electric else "DIESEL"
            quantity_key = "kWh" if use_electric else "Liters"
            values = [
                source["Year model"][i], technology, source["Truck cost"][i],
                source[quantity_key][i], source["Cost fuel"][i], source["Toll cost"][i],
                source["Daily cost"][i], source["Yearly cost"][i],
            ]
            for key, item in zip(truck, values):
                truck[key].append(item)
        truck["Total"] = sum(truck["Yearly cost"])
        trucks[name] = truck

    total = sum(truck["Total"] for truck in trucks.values())
    return {
        "Scenario years": list(diesel["Scenario years"]),
        "Transition plan": plan,
        "Trucks": trucks,
        "Total": total,
        "Summary": {
            "DIESEL": diesel["Total"],
            "ELECTRIC": electric["Total"],
            "TRANSITION": total,
            "Savings vs DIESEL": diesel["Total"] - total,
        },
    }


def candidate_years(model, truck_index):
    """Return replacement years in which a truck may become Electric."""
    replacement = int(model["common"]["replacement_frequency_years"])
    initial = int(model["fleet_initial_model_years"][truck_index])
    candidates, previous = [], None
    for year in scenario_years(model):
        current = active_model_year(year, initial, replacement)
        if (previous is None and current == year) or (previous is not None and current != previous):
            candidates.append(year)
        previous = current
    if model["optimization"]["allow_no_transition"]:
        candidates.append(None)
    return candidates


def transition_counts(plan):
    """Count how many trucks transition in each year."""
    counts = {}
    for year in plan.values():
        if year is not None:
            counts[year] = counts.get(year, 0) + 1
    return counts


def feasible(model, plan):
    """Check annual capacity and minimum-transition constraints."""
    settings = model["optimization"]
    counts = transition_counts(plan)
    limit = settings["max_transitions_per_year"]
    enough = sum(year is not None for year in plan.values()) >= int(settings["minimum_trucks_transitioned"])
    within_limit = limit is None or all(count <= int(limit) for count in counts.values())
    return enough and within_limit


def optimize_transition(model):
    """Search all feasible replacement-based plans and minimize total cost."""
    diesel = fleet_scenario(model, "DIESEL")
    electric = fleet_scenario(model, "ELECTRIC")
    names = list(diesel["Trucks"])
    candidates = {name: candidate_years(model, i) for i, name in enumerate(names)}
    best = {"cost": None, "plan": None, "scenario": None}

    def search(index, plan):
        # Recursive enumeration keeps the optimizer dependency-free.
        if index == len(names):
            if feasible(model, plan):
                scenario = transition_scenario(diesel, electric, plan)
                if best["cost"] is None or scenario["Total"] < best["cost"]:
                    best.update(cost=scenario["Total"], plan=dict(plan), scenario=scenario)
            return
        name = names[index]
        for year in candidates[name]:
            plan[name] = year
            search(index + 1, plan)
        del plan[name]

    search(0, {})
    if best["plan"] is None:
        raise ValueError("No feasible transition plan.")
    best["transition_counts"] = transition_counts(best["plan"])
    best["diesel_total"] = diesel["Total"]
    best["electric_total"] = electric["Total"]
    best["savings_vs_diesel"] = diesel["Total"] - best["cost"]
    return best


def evaluate(model):
    """Return the optimization metrics used by sensitivity analysis."""
    optimum = optimize_transition(model)
    return {
        "optimized_cost": optimum["cost"],
        "savings_vs_diesel": optimum["savings_vs_diesel"],
        "plan": optimum["plan"],
    }


def sensitivity_ranking(model):
    """Perturb one input at a time, re-optimize, and rank its impact."""
    settings = model["sensitivity"]
    objective = settings["objective"]
    base = evaluate(model)
    base_objective = float(base[objective])
    ranking = []

    for item in settings["variables"]:
        original = float(get_path(model, item["path"]))
        if "absolute_change" in item:
            tested = [original - float(item["absolute_change"]), original + float(item["absolute_change"])]
        else:
            change = float(item.get("relative_change", settings["relative_change"]))
            tested = [original * (1.0 - change), original * (1.0 + change)]
        low, high = min(tested), max(tested)

        outcomes = []
        for tested_value in [low, high]:
            changed_model = clone(model)
            set_path(changed_model, item["path"], tested_value)
            outcomes.append(evaluate(changed_model))

        low_objective, high_objective = float(outcomes[0][objective]), float(outcomes[1][objective])
        impact = max(abs(low_objective - base_objective), abs(high_objective - base_objective))
        ranking.append({
            "Variable": item["name"],
            "Base input": original,
            "Low input": low,
            "High input": high,
            "Base objective": base_objective,
            "Low objective": low_objective,
            "High objective": high_objective,
            "Maximum absolute impact": impact,
            "Normalized impact": impact / abs(base_objective) if base_objective else impact,
            "Low optimal plan": outcomes[0]["plan"],
            "High optimal plan": outcomes[1]["plan"],
        })

    ranking.sort(key=lambda row: row["Maximum absolute impact"], reverse=True)
    for rank, row in enumerate(ranking, 1):
        row["Rank"] = rank
    return ranking


def create_annual_matrix(model, technology, series=None):
    """Create the annual worksheet matrix as a two-dimensional list."""
    series = annual_series(model, technology) if series is None else series
    common, tech = model["common"], model["technology"][technology]
    labels, n = tech["labels"], len(series["Year"])
    factors = lambda curve: [factor(curve, i) for i in range(n)]
    return [
        ["Year", "Base/Calc"] + series["Year"],
        [labels["truck"], tech["truck_cost"]["base"]] + factors(tech["truck_cost"]),
        ["Incentives (trucks)", tech["truck_incentive"]["base"]] + factors(tech["truck_incentive"]),
        ["Cost per km equipment", "Calc"] + series["Cost per km equipment"],
        ["Daily cost equipment", ""] + series["Daily cost equipment"],
        ["Daily KM", common["daily_km"]["base"]] + factors(common["daily_km"]),
        [labels["efficiency"], tech["efficiency"]["base"]] + factors(tech["efficiency"]),
        [labels["daily_quantity"], "Calc"] + series["Daily energy quantity"],
        [labels["energy_price"], tech["energy_price"]["base"]] + factors(tech["energy_price"]),
        ["Incentives (fuel)", tech["energy_incentive"]["base"]] + factors(tech["energy_incentive"]),
        [labels["net_energy_price"], "Calc"] + series["Net energy unit cost"],
        ["Toll cost / km", tech["toll_rate"]["base"]] + factors(tech["toll_rate"]),
        ["Incentives (toll)", tech["toll_incentive"]["base"]] + factors(tech["toll_incentive"]),
        ["Daily cost toll", "Calc"] + series["Daily cost toll"],
    ]


def create_scenario_matrix(scenario):
    """Create a homogeneous fleet worksheet matrix."""
    years = scenario["Scenario years"]
    quantity = "Liters" if scenario["Technology"] == "DIESEL" else "kWh"
    metrics = ["Year model", "Truck cost", quantity, "Cost fuel", "Toll cost", "Daily cost", "Yearly cost"]
    matrix = [["Truck", "Metric"] + years + [str(len(years)) + " years total"]]
    for name, truck in scenario["Trucks"].items():
        for metric in metrics:
            matrix.append([name, metric] + truck[metric] + [truck["Total"] if metric == "Yearly cost" else ""])
    matrix.append(["TOTAL", "Scenario"] + [""] * len(years) + [scenario["Total"]])
    return matrix


def create_transition_matrix(transition):
    """Create the mixed-fleet worksheet matrix."""
    years = transition["Scenario years"]
    metrics = ["Year model", "Truck cost", "Liters/kWh", "Cost fuel", "Toll cost", "Daily cost", "Yearly cost"]
    matrix = [["Truck", "Metric"] + years + [str(len(years)) + " years total"]]
    for name, truck in transition["Trucks"].items():
        for metric in metrics:
            matrix.append([name, metric] + truck[metric] + [truck["Total"] if metric == "Yearly cost" else ""])
    matrix.append(["TOTAL", "Scenario"] + [""] * len(years) + [transition["Total"]])
    return matrix


def print_ranking(ranking, top=10):
    """Print the most influential variables in compact form."""
    print("\nSENSITIVITY RANKING")
    for row in ranking[:int(top)]:
        print(
            str(row["Rank"]) + ". " + row["Variable"]
            + " | impact: " + str(round(row["Maximum absolute impact"], 2))
            + " | normalized: " + str(round(row["Normalized impact"] * 100.0, 2)) + "%"
        )


# All assumptions, constraints and sensitivity variables are editable here.
MODEL = {
    "model_first_year": 1,
    "scenario": {"start_year": 5, "years": 10},
    "fleet_initial_model_years": [5, 2, 3, 4],
    "common": {
        "operating_days_per_year": 250,
        "replacement_frequency_years": 4,
        "daily_km": {"base": 500.0, "factor_start": 1.0, "factor_step": 0.0},
    },
    "technology": {
        "DIESEL": {
            "labels": {
                "truck": "Truck cost (Diesel)", "efficiency": "Consumption (km/liter)",
                "daily_quantity": "Liters/day", "quantity": "Liters",
                "energy_price": "Diesel cost per liter", "net_energy_price": "Net cost of fuel/liter",
            },
            "truck_cost": {"base": 137000.0, "factor_start": 1.0, "factor_step": 0.01},
            "truck_incentive": {"base": -5000.0, "factor_start": 1.0, "factor_step": 0.0},
            "efficiency": {"base": 3.5, "factor_start": 1.0, "factor_step": -0.005},
            "energy_price": {"base": 1.5, "factor_start": 1.0, "factor_step": 0.02},
            "energy_incentive": {"base": -0.15, "factor_start": 1.0, "factor_step": 0.0},
            "toll_rate": {"base": 0.3, "factor_start": 1.0, "factor_step": 0.02},
            "toll_incentive": {"base": -0.02, "factor_start": 1.0, "factor_step": 0.0},
        },
        "ELECTRIC": {
            "labels": {
                "truck": "Truck cost (Electric)", "efficiency": "Consumption (km/kWh)",
                "daily_quantity": "kWh/day", "quantity": "kWh",
                "energy_price": "kWh cost", "net_energy_price": "Net cost of fuel/kWh",
            },
            "truck_cost": {"base": 278000.0, "factor_start": 1.0, "factor_step": -0.01},
            "truck_incentive": {"base": -5000.0, "factor_start": 1.0, "factor_step": 0.0},
            "efficiency": {"base": 0.8064516129032258, "factor_start": 1.0, "factor_step": -0.005},
            "energy_price": {"base": 0.11510299571428571, "factor_start": 1.0, "factor_step": 0.02},
            "energy_incentive": {"base": -0.01, "factor_start": 1.0, "factor_step": 0.0},
            "toll_rate": {"base": 0.3, "factor_start": 0.32, "factor_step": 0.02},
            "toll_incentive": {"base": -0.04, "factor_start": 1.0, "factor_step": 0.0},
        },
    },
    "optimization": {
        "allow_no_transition": True,
        "minimum_trucks_transitioned": 0,
        "max_transitions_per_year": None,
    },
    "sensitivity": {
        "relative_change": 0.10,
        "objective": "savings_vs_diesel",
        "variables": [
            {"name": "Daily distance", "path": ["common", "daily_km", "base"]},
            {"name": "Operating days", "path": ["common", "operating_days_per_year"]},
            {"name": "Replacement frequency", "path": ["common", "replacement_frequency_years"], "absolute_change": 1.0},
            {"name": "Diesel truck cost", "path": ["technology", "DIESEL", "truck_cost", "base"]},
            {"name": "Diesel truck cost trend", "path": ["technology", "DIESEL", "truck_cost", "factor_step"], "absolute_change": 0.005},
            {"name": "Diesel truck incentive", "path": ["technology", "DIESEL", "truck_incentive", "base"]},
            {"name": "Diesel efficiency", "path": ["technology", "DIESEL", "efficiency", "base"]},
            {"name": "Diesel efficiency trend", "path": ["technology", "DIESEL", "efficiency", "factor_step"], "absolute_change": 0.0025},
            {"name": "Diesel fuel price", "path": ["technology", "DIESEL", "energy_price", "base"]},
            {"name": "Diesel fuel price trend", "path": ["technology", "DIESEL", "energy_price", "factor_step"], "absolute_change": 0.01},
            {"name": "Diesel fuel incentive", "path": ["technology", "DIESEL", "energy_incentive", "base"]},
            {"name": "Diesel toll", "path": ["technology", "DIESEL", "toll_rate", "base"]},
            {"name": "Diesel toll trend", "path": ["technology", "DIESEL", "toll_rate", "factor_step"], "absolute_change": 0.01},
            {"name": "Diesel toll incentive", "path": ["technology", "DIESEL", "toll_incentive", "base"]},
            {"name": "Electric truck cost", "path": ["technology", "ELECTRIC", "truck_cost", "base"]},
            {"name": "Electric truck cost trend", "path": ["technology", "ELECTRIC", "truck_cost", "factor_step"], "absolute_change": 0.005},
            {"name": "Electric truck incentive", "path": ["technology", "ELECTRIC", "truck_incentive", "base"]},
            {"name": "Electric efficiency", "path": ["technology", "ELECTRIC", "efficiency", "base"]},
            {"name": "Electric efficiency trend", "path": ["technology", "ELECTRIC", "efficiency", "factor_step"], "absolute_change": 0.0025},
            {"name": "Electricity price", "path": ["technology", "ELECTRIC", "energy_price", "base"]},
            {"name": "Electricity price trend", "path": ["technology", "ELECTRIC", "energy_price", "factor_step"], "absolute_change": 0.01},
            {"name": "Electric energy incentive", "path": ["technology", "ELECTRIC", "energy_incentive", "base"]},
            {"name": "Electric toll", "path": ["technology", "ELECTRIC", "toll_rate", "base"]},
            {"name": "Electric toll trend", "path": ["technology", "ELECTRIC", "toll_rate", "factor_step"], "absolute_change": 0.01},
            {"name": "Electric toll incentive", "path": ["technology", "ELECTRIC", "toll_incentive", "base"]},
        ],
    },
}


if __name__ == "__main__":
    diesel_series = annual_series(MODEL, "DIESEL")
    electric_series = annual_series(MODEL, "ELECTRIC")
    diesel = fleet_scenario(MODEL, "DIESEL", diesel_series)
    electric = fleet_scenario(MODEL, "ELECTRIC", electric_series)
    transition = transition_scenario(diesel, electric, MODEL["scenario"]["start_year"])

    # Same worksheet-style matrices as the original program.
    diesel_annual_matrix = create_annual_matrix(MODEL, "DIESEL", diesel_series)
    electric_annual_matrix = create_annual_matrix(MODEL, "ELECTRIC", electric_series)
    diesel_fleet_matrix = create_scenario_matrix(diesel)
    electric_fleet_matrix = create_scenario_matrix(electric)
    transition_matrix = create_transition_matrix(transition)

    optimum = optimize_transition(MODEL)
    ranking = sensitivity_ranking(MODEL)

    print("DIESEL:", diesel["Total"])
    print("ELECTRIC:", electric["Total"])
    print("TRANSITION:", transition["Total"])
    print("OPTIMAL TRANSITION:", optimum["cost"])
    print("OPTIMAL PLAN:", optimum["plan"])
    print("TRANSITIONS BY YEAR:", optimum["transition_counts"])
    print("SAVINGS VS DIESEL:", optimum["savings_vs_diesel"])
    print_ranking(ranking)
