
# n=2 # world size
# part=2 # partitions
# for ((p=0;p<n;p++)); do
#     echo $p
#     python3 examples/train_homogeneous_graph_advanced_new.py --cpu-run --partitioning-json-file ogbn-arxiv.json \
#              --train-iters 300 --rank $p --backend ccl --world-size 2 \
#              --enable_cr --compression_type variable \
#              --train-mode one_shot_aggregation \
#              --max_comp_ratio 128 --min_comp_ratio 1.5\
#              --variable_compression_slope 5 \
#              --gnn-layer sage --part-method random\
#              --partitions $part &
# done

n=16 # world size
part=16 # partitions
for ((p=0;p<n;p++)); do
    echo $p
    python3 examples/train_homogeneous_graph_advanced_new.py --cpu-run --partitioning-json-file ogbn-products.json \
             --train-iters 300 --rank $p --backend ccl --world-size 16 \
             --enable_cr --compression_type variable \
             --train-mode one_shot_aggregation \
             --max_comp_ratio 128 --min_comp_ratio 1.5\
             --variable_compression_slope 5 \
             --gnn-layer sage --part-method random\
             --partitions $part &
done
