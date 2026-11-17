#!/bin/bash
# 16 Gurobi runs: {zones4, hybrid25} x {base, re100-annual+nuc} x {no wave, aggressive, moderate, conservative}
cd ~/Downloads/_gh_compare/repo
PY=pypsa-usa/.venv/bin/python
unset CA_SOLVER
for net in zones4 hybrid25; do
  for pol in base re100; do
    [ $pol = re100 ] && P="--renewables annual --renewable-share 1.0 --nuclear-counts" || P=""
    for w in none aggressive moderate conservative; do
      [ $w = none ] && W="" || W="--wave $w"
      echo "=== $(date +%H:%M) $net $pol $w"
      $PY notebooks/run_scenario.py --network $net $P $W --tag gurobi > ../logs/gurobi_${net}_${pol}_${w}.log 2>&1 \
        && grep -E "system cost|wave GW|wave TWh|CO2 price" ../logs/gurobi_${net}_${pol}_${w}.log | tail -4 \
        || { echo "FAILED"; tail -5 ../logs/gurobi_${net}_${pol}_${w}.log; }
    done
  done
done
echo "=== done $(date +%H:%M)"
