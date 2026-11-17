import sys, pypsa, pandas as pd
n = pypsa.Network(sys.argv[1])
w = n.snapshot_weightings.generators
g = n.generators[n.generators.carrier == "wave"]
print(g[["bus", "p_nom_opt", "p_nom_max", "capital_cost", "marginal_cost"]])
lmp = n.buses_t.marginal_price
for gen, r in g.iterrows():
    cf = n.get_switchable_as_dense("Generator", "p_max_pu")[gen]
    link = n.links.loc[f"{gen} export"]
    hub_val = (lmp[r.bus] * cf * w).sum()                          # $/MW-yr earned at the hub price
    land_val = (lmp[link.bus1] * cf * link.efficiency * w).sum()   # $/MW-yr at the landing node
    print(f"{gen}: CF {(cf*w).sum()/w.sum():.3f}  energy value at landing {land_val/1e3:.0f} k$/MW-yr, "
          f"at hub {hub_val/1e3:.0f}, cost {r.capital_cost/1e3:.0f} + cable {link.capital_cost/1e3:.0f} k$/MW-yr")
# reserve-margin and RE-constraint duals, if any
gc = n.global_constraints
print(gc[["type", "carrier_attribute", "constant", "mu"]] if len(gc) else "no global constraints")
print("renewable-type carriers in model:", sorted(n.carriers.index))
