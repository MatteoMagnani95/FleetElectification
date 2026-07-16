def constant_vector(value, n):
    return [float(value) for _ in range(int(n))]


def linear_vector(start, step, n):
    return [float(start) + float(step) * i for i in range(int(n))]


def get_technology_keys(data):
    if "Truck cost (Diesel)" in data:
        return {"truck": "Truck cost (Diesel)", "consumption": "Consumtpion (km/liter)", "quantity": "Liters/day",
            "quantity_scenario": "Liters", "fuel": "Diesel cost per liter", "net_fuel": "Net cost of fuel/liter", }
    return {"truck": "Truck cost (Electric)", "consumption": "Consumtpion (km/kWh)", "quantity": "kWh/day",
        "quantity_scenario": "kWh", "fuel": "kWh cost", "net_fuel": "Net cost of fuel/kWh", }


def get_year_index(years, year):
    for i in range(len(years)):
        if int(years[i]) == int(year):
            return i
    raise ValueError("Year not available in the model: " + str(year))


def get_value_for_year(series, key, year):
    return float(series[key][get_year_index(series["Year"], year)])


def calculate_annual_series(data):
    """
    Reproduces the formulas in the annual Diesel and Electric tables.
    """
    k = get_technology_keys(data)
    years = [year for year in data["Year"]]
    base_daily_km = float(data["Daily KM"]["Base"])
    operating_days = int(data["Operating days per year"])
    replacement_frequency = int(data["Frequency replacement (years)"])

    lifecycle_km = base_daily_km * operating_days * replacement_frequency

    equipment_costs_per_km = []
    daily_equipment_costs = []
    daily_quantities = []
    net_energy_costs = []
    daily_toll_costs = []

    for i in range(len(years)):
        equipment_cost = (float(data[k["truck"]]["Base"]) * float(data[k["truck"]]["Year factor"][i]) + float(
            data["Incentives (trucks)"]["Base"]) * float(data["Incentives (trucks)"]["Year factor"][i]))

        equipment_cost_per_km = equipment_cost / lifecycle_km
        equipment_costs_per_km.append(equipment_cost_per_km)

        # As in the workbook, this calculation uses the base Daily KM value.
        daily_equipment_costs.append(equipment_cost_per_km * base_daily_km)

        daily_quantities.append(
            base_daily_km * float(data["Daily KM"]["Year factor"][i]) / float(data[k["consumption"]]["Base"]) * float(
                data[k["consumption"]]["Year factor"][i]))

        net_energy_costs.append(float(data[k["fuel"]]["Base"]) * float(data[k["fuel"]]["Year factor"][i]) + float(
            data["Incentives (fuel)"]["Base"]) * float(data["Incentives (fuel)"]["Year factor"][i]))

        daily_toll_costs.append((float(data["Toll cost / km"]["Base"]) * float(
            data["Toll cost / km"]["Year factor"][i]) + float(data["Incentives (toll)"]["Base"]) * float(
            data["Incentives (toll)"]["Year factor"][i])) * base_daily_km * float(data["Daily KM"]["Year factor"][i]))

    return {"Technology": data["Technology"], "Lifecycle KM": lifecycle_km, "Operating days per year": operating_days,
        "Frequency replacement (years)": replacement_frequency, "Year": years,
        "Cost per km equipment": equipment_costs_per_km, "Daily cost equipment": daily_equipment_costs,
        k["quantity"]: daily_quantities, k["net_fuel"]: net_energy_costs, "Daily cost toll": daily_toll_costs,
        "_keys": k, }


def get_model_year(scenario_year, initial_year, replacement_frequency):
    """
    Example: initial year 2, replacement frequency 4, scenario years 5..14
    -> 2, 6, 6, 6, 6, 10, 10, 10, 10, 14.
    """
    replacements = (int(scenario_year) - int(initial_year)) // int(replacement_frequency)
    if replacements < 0:
        replacements = 0
    return int(initial_year) + replacements * int(replacement_frequency)


def calculate_fleet_scenario(data, series):
    """
    Reproduces the 10-year Diesel or Electric matrix.
    """
    start = int(data["Scenario start year"])
    duration = int(data["Scenario years"])
    scenario_years = [start + i for i in range(duration)]
    replacement_frequency = int(data["Frequency replacement (years)"])
    k = series["_keys"]

    trucks = {}
    scenario_total = 0.0

    for truck_index in range(len(data["Fleet initial model year"])):
        truck_name = "Truck " + str(truck_index + 1)
        initial_model_year = int(data["Fleet initial model year"][truck_index])

        result = {"Year model": [], "Truck cost": [], k["quantity_scenario"]: [], "Cost fuel": [], "Toll cost": [],
            "Daily cost": [], "Yearly cost": [], }

        for year in scenario_years:
            model_year = get_model_year(year, initial_model_year, replacement_frequency, )

            equipment_cost = get_value_for_year(series, "Daily cost equipment", model_year, )
            daily_quantity = get_value_for_year(series, k["quantity"], model_year, )
            unit_energy_cost = get_value_for_year(series, k["net_fuel"], year, )
            toll_cost = get_value_for_year(series, "Daily cost toll", year, )

            fuel_cost = daily_quantity * unit_energy_cost
            daily_cost = (equipment_cost + fuel_cost + toll_cost)
            yearly_cost = (daily_cost * int(data["Operating days per year"]))

            result["Year model"].append(model_year)
            result["Truck cost"].append(equipment_cost)
            result[k["quantity_scenario"]].append(daily_quantity)
            result["Cost fuel"].append(fuel_cost)
            result["Toll cost"].append(toll_cost)
            result["Daily cost"].append(daily_cost)
            result["Yearly cost"].append(yearly_cost)

        result["10 years total"] = sum(result["Yearly cost"])
        scenario_total += result["10 years total"]
        trucks[truck_name] = result

    return {"Scenario years": scenario_years, "Trucks": trucks, "10 years scenario": scenario_total, }


def calculate_transition_scenario(scenario_diesel, scenario_electric, transition_start_year, ):
    """
    A truck switches to Electric when its model year becomes
    greater than or equal to the transition start year.
    """
    trucks = {}
    transition_total = 0.0

    for truck_name in scenario_diesel["Trucks"]:
        diesel = scenario_diesel["Trucks"][truck_name]
        electric = scenario_electric["Trucks"][truck_name]

        result = {"Year model": [x for x in diesel["Year model"]], "Technology": [], "Truck cost": [], "Liters/kWh": [],
            "Cost fuel": [], "Toll cost": [], "Daily cost": [], "Yearly cost": [], }

        for i in range(len(diesel["Year model"])):
            if int(diesel["Year model"][i]) >= int(transition_start_year):
                source_scenario = electric
                quantity_key = "kWh"
                technology = "ELECTRIC"
            else:
                source_scenario = diesel
                quantity_key = "Liters"
                technology = "DIESEL"

            result["Technology"].append(technology)
            result["Truck cost"].append(source_scenario["Truck cost"][i])
            result["Liters/kWh"].append(source_scenario[quantity_key][i])
            result["Cost fuel"].append(source_scenario["Cost fuel"][i])
            result["Toll cost"].append(source_scenario["Toll cost"][i])
            result["Daily cost"].append(source_scenario["Daily cost"][i])
            result["Yearly cost"].append(source_scenario["Yearly cost"][i])

        result[f"{len(diesel["Year model"])} years total"] = sum(result["Yearly cost"])
        transition_total += result["10 years total"]
        trucks[truck_name] = result

    return {"Scenario years": [x for x in scenario_diesel["Scenario years"]], "Trucks": trucks,
        "10 years scenario": transition_total,
        "Summary": {"DIESEL": scenario_diesel["10 years scenario"], "ELECTRIC": scenario_electric["10 years scenario"],
            "TRANSITION": transition_total, "Variance vs DIESEL - ELECTRIC": (
                    scenario_electric["10 years scenario"] - scenario_diesel["10 years scenario"]),
            "Variance vs DIESEL - TRANSITION": (transition_total - scenario_diesel["10 years scenario"]), }, }


def create_annual_matrix(data, series):
    """
    Two-dimensional list corresponding to the annual worksheet table.
    """
    k = series["_keys"]
    return [["Year", "Base/Calc"] + [x for x in data["Year"]],
            [k["truck"], data[k["truck"]]["Base"]] + [x for x in data[k["truck"]]["Year factor"]],
            ["Incentives (trucks)", data["Incentives (trucks)"]["Base"]] + [x for x in
                                                                            data["Incentives (trucks)"]["Year factor"]],
            ["Cost per km equipment", "Calc"] + [x for x in series["Cost per km equipment"]],
            ["Daily cost equipment", ""] + [x for x in series["Daily cost equipment"]],
            ["Daily KM", data["Daily KM"]["Base"]] + [x for x in data["Daily KM"]["Year factor"]],
            [k["consumption"], data[k["consumption"]]["Base"]] + [x for x in data[k["consumption"]]["Year factor"]],
            [k["quantity"], "Calc"] + [x for x in series[k["quantity"]]],
            [k["fuel"], data[k["fuel"]]["Base"]] + [x for x in data[k["fuel"]]["Year factor"]],
            ["Incentives (fuel)", data["Incentives (fuel)"]["Base"]] + [x for x in
                                                                        data["Incentives (fuel)"]["Year factor"]],
            [k["net_fuel"], "Calc"] + [x for x in series[k["net_fuel"]]],
            ["Toll cost / km", data["Toll cost / km"]["Base"]] + [x for x in data["Toll cost / km"]["Year factor"]],
            ["Incentives (toll)", data["Incentives (toll)"]["Base"]] + [x for x in
                                                                        data["Incentives (toll)"]["Year factor"]],
            ["Daily cost toll", "Calc"] + [x for x in series["Daily cost toll"]], ]


def create_scenario_matrix(scenario):
    """
    Two-dimensional list corresponding to the 10-year matrix.
    """
    matrix = [["Truck", "Metric"] + [x for x in scenario["Scenario years"]] + ["10 years total"]]

    for truck_name in scenario["Trucks"]:
        truck = scenario["Trucks"][truck_name]
        quantity_key = "Liters" if "Liters" in truck else "kWh"

        for key in ["Year model", "Truck cost", quantity_key, "Cost fuel", "Toll cost", "Daily cost", "Yearly cost", ]:
            row_total = (truck["10 years total"] if key == "Yearly cost" else "")
            matrix.append([truck_name, key] + [x for x in truck[key]] + [row_total])

    matrix.append(
        ["TOTAL", "10 years scenario"] + ["" for _ in scenario["Scenario years"]] + [scenario["10 years scenario"]])
    return matrix


def create_transition_matrix(transition):
    """
    Two-dimensional list in the same order as the Transition worksheet.
    Technology remains available in the dictionary, but it is not
    added as a row because it is not present in the Excel worksheet.
    """
    matrix = [["Truck", "Metric"] + [x for x in transition["Scenario years"]] + ["10 years total"]]

    for truck_name in transition["Trucks"]:
        truck = transition["Trucks"][truck_name]

        for key in ["Year model", "Truck cost", "Liters/kWh", "Cost fuel", "Toll cost", "Daily cost", "Yearly cost", ]:
            row_total = (truck["10 years total"] if key == "Yearly cost" else "")
            matrix.append([truck_name, key] + [x for x in truck[key]] + [row_total])

    matrix.append(
        ["TOTAL", "10 years scenario"] + ["" for _ in transition["Scenario years"]] + [transition["10 years scenario"]])
    return matrix


MODEL = {
    "DIESEL": {"Technology": "DIESEL", "Lifecycle KM": 500000.0, "Operating days per year": 250,
        "Frequency replacement (years)": 4, "Year": [year for year in range(1, 15)],

        "Truck cost (Diesel)": {"Base": 137000.0, "Year factor": linear_vector(1.0, 0.01, 14), },
        "Incentives (trucks)": {"Base": -5000.0, "Year factor": constant_vector(1.0, 14)},
        "Daily KM": {"Base": 500.0, "Year factor": constant_vector(1.0, 14)},
        "Consumption (km/liter)": {"Base": 3.5, "Year factor": linear_vector(1.0, -0.005, 14)},
        "Diesel cost per liter": {"Base": 1.5, "Year factor": linear_vector(1.0, 0.02, 14)},
        "Incentives (fuel)": {"Base": -0.15, "Year factor": constant_vector(1.0, 14)},
        "Toll cost / km": {"Base": 0.3, "Year factor": linear_vector(1.0, 0.02, 14)},
        "Incentives (toll)": {"Base": -0.02, "Year factor": constant_vector(1.0, 14)},
        "Scenario start year": 5, "Scenario years": 10, "Fleet initial model year": [5, 2, 3, 4]},

    "ELECTRIC": {"Technology": "ELECTRIC", "Lifecycle KM": 500000.0, "Operating days per year": 250,
        "Frequency replacement (years)": 4, "Year": [year for year in range(1, 15)],

        "Truck cost (Electric)": {"Base": 278000.0, "Year factor": linear_vector(1.0, -0.01, 14)},
        "Incentives (trucks)": {"Base": -5000.0, "Year factor": constant_vector(1.0, 14)},
        "Daily KM": {"Base": 500.0, "Year factor": constant_vector(1.0, 14)},
        "Consumption (km/kWh)": {"Base": 0.8064516129032258, "Year factor": linear_vector(1.0, -0.005, 14)},
        "kWh cost": {"Base": 0.11510299571428571, "Year factor": linear_vector(1.0, 0.02, 14)},
        "Incentives (fuel)": {"Base": -0.01, "Year factor": constant_vector(1.0, 14)},
        "Toll cost / km": {"Base": 0.3, "Year factor": linear_vector(0.32, 0.02, 14)},
        "Incentives (toll)": {"Base": -0.04, "Year factor": constant_vector(1.0, 14)},

        "Scenario start year": 5, "Scenario years": 10, "Fleet initial model year": [5, 2, 3, 4]}}

diesel_annual_series = calculate_annual_series(MODEL["DIESEL"])
electric_annual_series = calculate_annual_series(MODEL["ELECTRIC"])

diesel_scenario = calculate_fleet_scenario(MODEL["DIESEL"], diesel_annual_series, )
electric_scenario = calculate_fleet_scenario(MODEL["ELECTRIC"], electric_annual_series, )
transition_scenario = calculate_transition_scenario(diesel_scenario, electric_scenario,
    MODEL["DIESEL"]["Scenario start year"], )

# Final matrices, all represented as two-dimensional lists.
diesel_annual_matrix = create_annual_matrix(MODEL["DIESEL"], diesel_annual_series, )
electric_annual_matrix = create_annual_matrix(MODEL["ELECTRIC"], electric_annual_series, )
diesel_fleet_matrix = create_scenario_matrix(diesel_scenario)
electric_fleet_matrix = create_scenario_matrix(electric_scenario)
transition_matrix = create_transition_matrix(transition_scenario)

print("DIESEL:", diesel_scenario["10 years scenario"])
print("ELECTRIC:", electric_scenario["10 years scenario"])
print("TRANSITION:", transition_scenario["10 years scenario"])
print("SUMMARY:", transition_scenario["Summary"])
