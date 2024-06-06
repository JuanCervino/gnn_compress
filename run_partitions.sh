# Partition the graph
# python3 examples/partition_arxiv_products.py --dataset-name ogbn-arxiv --num-partitions 2
# python3 examples/partition_arxiv_products.py --dataset-name ogbn-arxiv --num-partitions 2 --part-method metis


python3 examples/partition_arxiv_products.py --dataset-name ogbn-papers100M --num-partitions 16 --part-method random
python3 examples/partition_arxiv_products.py --dataset-name ogbn-papers100M --num-partitions 16 --part-method metis

