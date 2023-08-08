# Copyright (c) 2022 Intel Corporation

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from typing import List, Union, Dict
from argparse import ArgumentParser
import os
import logging
import time
import torch
import torch.nn.functional as F
from torch import nn
import torch.distributed as dist
import dgl  # type: ignore
from datetime import datetime
import json
import sar


parser = ArgumentParser(
    description="GNN training on node classification tasks in homogeneous graphs")


parser.add_argument(
    "--partitioning-json-file",
    type=str,
    default="",
    help="Path to the .json file containing partitioning information "
)

parser.add_argument('--ip-file', default='./ip_file', type=str,
                    help='File with ip-address. Worker 0 creates this file and all others read it ')

parser.add_argument('--output-dir', default='./experiments', type=str,
                    help=' Saving folder ')

parser.add_argument('--backend', default='nccl', type=str, choices=['ccl', 'nccl', 'mpi'],
                    help='Communication backend to use '
                    )

parser.add_argument(
    "--cpu-run", action="store_true",
    help="Run on CPUs if set, otherwise run on GPUs "
)

parser.add_argument(
    "--disable-cut-edges", action="store_false",
    help="Disable Communication "
)

parser.add_argument('--train-iters', default=100, type=int,
                    help='number of training iterations ')

parser.add_argument(
    "--lr",
    type=float,
    default=1e-2,
    help="learning rate"
)


parser.add_argument('--rank', default=0, type=int,
                    help='Rank of the current worker ')

parser.add_argument('--world-size', default=2, type=int,
                    help='Number of workers ')

parser.add_argument('--partitions', default=0, type=int,
                    help='Number of partitions. By default it will be equal to world-size.')

parser.add_argument('--hidden-layer-dim', default=256, type=int,
                    help='Dimension of GNN hidden layer')

parser.add_argument(
    "--part-method",
    type=str,
    default="random",
    choices=['random', 'metis'],
    help=" Form of graph partition. "
)

parser.add_argument(
    "--dataset-name",
    type=str,
    default="ogbn-arxiv",
    choices=['ogbn-arxiv', 'ogbn-products'],
    help="Dataset name. ogbn-arxiv or ogbn-products "
)

# class GNNModel(nn.Module):
#     def __init__(self,  in_dim: int, hidden_dim: int, out_dim: int):
#         super().__init__()

#         self.convs = nn.ModuleList([
#             # pylint: disable=no-member
#             dgl.nn.SAGEConv(in_dim, hidden_dim, aggregator_type='mean'),
#             # pylint: disable=no-member
#             dgl.nn.SAGEConv(hidden_dim, hidden_dim, aggregator_type='mean'),
#             # pylint: disable=no-member
#             dgl.nn.SAGEConv(hidden_dim, out_dim, aggregator_type='mean'),
#         ])
#         self.top = nn.Sequential(
#                                 nn.BatchNorm1d(hidden_dim),
#                                 # nn.ReLU(),
#                                 nn.Dropout(0.5),
#         )

    
#     def forward(self,  graph: sar.GraphShardManager, features: torch.Tensor):
#         for idx, conv in enumerate(self.convs):
#             features = conv(graph, features)
#             if idx < len(self.convs) - 1:
#                 features = self.top(features)
#             features = F.relu(features, inplace=True)
            
#         return features

class GNNModel(torch.nn.Module):
    def __init__(self,  in_dim: int, hidden_dim: int, out_dim: int):

        super(GNNModel, self).__init__()
        # To do - parameterize this
        self.dropout = 0.5
        self.num_layers = 3

        self.convs = torch.nn.ModuleList()
        self.convs.append(dgl.nn.SAGEConv(in_dim, hidden_dim, aggregator_type='mean'))
        self.bns = torch.nn.ModuleList()
        # self.bns.append(torch.nn.BatchNorm1d(hidden_dim))
        self.bns.append(sar.DistributedBN1D(hidden_dim, affine=True))

        for _ in range(self.num_layers - 2):
            self.convs.append(dgl.nn.SAGEConv(hidden_dim, hidden_dim, aggregator_type='mean'))
            # self.bns.append(torch.nn.BatchNorm1d(hidden_dim))
            self.bns.append(sar.DistributedBN1D(hidden_dim, affine=True))

        self.convs.append(dgl.nn.SAGEConv(hidden_dim, out_dim, aggregator_type='mean'))


    def reset_parameters(self):
        for conv in self.convs:
            conv.reset_parameters()
        for bn in self.bns:
            bn.reset_parameters()

    def forward(self, adj_t, x):
        for i, conv in enumerate(self.convs[:-1]):
            x = conv(adj_t, x)
            x = self.bns[i](x)
            x = F.relu(x)
            x = F.dropout(x, p=self.dropout, training=self.training)
        x = self.convs[-1](adj_t, x)
        return x.log_softmax(dim=-1)

def main():
    args = parser.parse_args()
    # Make the number of partitions equal to the world size
    if args.partitions == 0:
        args.partitions = args.world_size
    # Make sure that there are more partitions than agents in the world.
    assert(args.partitions >= args.world_size)

    args.output_dir = os.path.join(args.output_dir, args.dataset_name,args.part_method,'disable_comm_'+str(args.disable_cut_edges)+'_partitions_'+str(args.partitions)+'_world_size_'+str(args.world_size)+'_'+ datetime.now().strftime("%Y-%m%d-%H%M") )
    os.makedirs(os.path.join(args.output_dir), exist_ok=True)

    # Save the dict
    if args.rank == 0:
        print('Args:')
        for k, v in sorted(vars(args).items()):
            print(f'\t{k}: {v}')

        with open(os.path.join(args.output_dir, 'args.json'), 'w') as f:
            json.dump(args.__dict__, f, indent=2)

    use_gpu = torch.cuda.is_available() and not args.cpu_run
    device = torch.device('cuda' if use_gpu else 'cpu')

    # Obtain the ip address of the master through the network file system
    master_ip_address = sar.nfs_ip_init(args.rank, args.ip_file)
    sar.initialize_comms(args.rank,
                         args.world_size, master_ip_address,
                         args.backend)


    partitioning_json_file = os.path.join('partition_data',args.part_method,args.dataset_name,str(args.partitions),args.partitioning_json_file)
    
    # Load DGL partition data
    partition_data = sar.load_dgl_partition_data(
        partitioning_json_file, args.rank, args.disable_cut_edges, device)

    # Obtain train,validation, and test masks
    # These are stored as node features. Partitioning may prepend
    # the node type to the mask names. So we use the convenience function
    # suffix_key_lookup to look up the mask name while ignoring the
    # arbitrary node type
    masks = {}
    for mask_name, indices_name in zip(['train_mask', 'val_mask', 'test_mask'],
                                       ['train_indices', 'val_indices', 'test_indices']):
        boolean_mask = sar.suffix_key_lookup(partition_data.node_features,
                                             mask_name)
        masks[indices_name] = boolean_mask.nonzero(
            as_tuple=False).view(-1).to(device)

    labels = sar.suffix_key_lookup(partition_data.node_features,
                                   'labels').long().to(device)

    # Obtain the number of classes by finding the max label across all workers
    num_labels = labels.max() + 1
    sar.comm.all_reduce(num_labels, dist.ReduceOp.MAX, move_to_comm_device=True)
    num_labels = num_labels.item() 
    
    features = sar.suffix_key_lookup(partition_data.node_features, 'features').to(device)
    full_graph_manager = sar.construct_full_graph(partition_data).to(device)
    # full_graph_manager = sar.construct_full_graph(partition_data, args.partitions).to(device)

    #We do not need the partition data anymore
    del partition_data
    
    gnn_model = GNNModel(features.size(1),
                         args.hidden_layer_dim,
                         num_labels).to(device)
    print('model', gnn_model)

    # Synchronize the model parmeters across all workers
    sar.sync_params(gnn_model)

    # Obtain the number of labeled nodes in the training
    # This will be needed to properly obtain a cross entropy loss
    # normalized by the number of training examples
    n_train_points = torch.LongTensor([masks['train_indices'].numel()])
    sar.comm.all_reduce(n_train_points, op=dist.ReduceOp.SUM, move_to_comm_device=True)
    n_train_points = n_train_points.item()

    optimizer = torch.optim.Adam(gnn_model.parameters(), lr=args.lr)
    for train_iter_idx in range(args.train_iters):
        # Train
        t_1 = time.time()
        logits = gnn_model(full_graph_manager, features)
        loss = F.cross_entropy(logits[masks['train_indices']],
                               labels[masks['train_indices']], reduction='sum')/n_train_points

        optimizer.zero_grad()
        loss.backward()
        # Do not forget to gather the parameter gradients from all workers
        sar.gather_grads(gnn_model)
        optimizer.step()
        train_time = time.time() - t_1

        # Calculate accuracy for train/validation/test
        results = []
        gnn_model.eval()
        with torch.no_grad():
            for indices_name in ['train_indices', 'val_indices', 'test_indices']:
                n_correct = (logits[masks[indices_name]].argmax(1) ==
                            labels[masks[indices_name]]).float().sum()
                results.extend([n_correct, masks[indices_name].numel()])
        gnn_model.train()

        acc_vec = torch.FloatTensor(results)
        # Sum the n_correct, and number of mask elements across all workers
        sar.comm.all_reduce(acc_vec, op=dist.ReduceOp.SUM, move_to_comm_device=True)
        (train_acc, val_acc, test_acc) =  \
            (acc_vec[0] / acc_vec[1],
             acc_vec[2] / acc_vec[3],
             acc_vec[4] / acc_vec[5])

        result_message = (
            f"iteration [{train_iter_idx}/{args.train_iters}] | "
        )
        result_message += ', '.join([
            f"train loss={loss:.4f}, "
            f"Accuracy: "
            f"train={train_acc:.4f} "
            f"valid={val_acc:.4f} "
            f"test={test_acc:.4f} "
            f" | train time = {train_time} "
            f" |"
        ])
        print(result_message, flush=True)
        # write file
        with open(os.path.join(args.output_dir, 'loss.txt'), 'a') as f:
            f.write(result_message+'\n')
            f.close()

if __name__ == '__main__':
    main()
