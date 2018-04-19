"""
Filter BAM. Filters for both read quality as well as barcodes, producing a set of BAMs.

This adapts the cellranger filter_reads stage to also split based on barcode clusters


"""
import itertools
import pandas as pd
import pysam
import tenkit.bam as tk_bam
import cellranger.utils as cr_utils


__MRO__ = """
stage FILTER_BAM(
    in bam possorted_bam,
    in csv barcode_clusters,
    out bam[] filtered_bams,
    out int[] clusters,
    src py "stages/filter_bam",
) split (
    in string locus,
    in string[] barcodes,
    out bam filtered_bam,
) using (
    mem_gb = 8,
)
"""


def split(args):
    df = pd.read_csv(args.barcode_clusters)
    # construct BAM chunks
    loci = build_loci(args.possorted_bam)
    # nest BAM chunks with clusters
    bc_chunks = []
    for cluster_id, d in df.groupby('Cluster'):
        for locus in loci:
            bc_chunks.append({'locus': locus, 'cluster_bcs': d.Barcode.tolist(), 'cluster_id': cluster_id,
                              '__mem_gb': 8})
    return {'chunks': bc_chunks}


def main(args, outs):
    outs.coerce_strings()

    in_bam = tk_bam.create_bam_infile(args.possorted_bam)
    out_bam, _ = tk_bam.create_bam_outfile(outs.filtered_bam, None, None, template=in_bam)
    cluster_bcs = set(args.cluster_bcs)

    # parse loci - could be 1 or more
    loci = [x.split(',') for x in args.locus.split(':')]
    for chrom, start, stop in loci:
        bam_iter = in_bam.fetch(chrom, int(start), int(stop), multiple_iterators=True)
        for (tid, pos), reads_iter in itertools.groupby(bam_iter, key=cr_utils.pos_sort_key):
            dupe_keys = set()
            for read in reads_iter:
                if cr_utils.get_read_barcode(read) not in cluster_bcs:
                    continue

                if cr_utils.is_read_dupe_candidate(read, cr_utils.get_high_conf_mapq({"high_conf_mapq":60})):
                    dupe_key = (cr_utils.si_pcr_dupe_func(read), cr_utils.get_read_umi(read))
                    if dupe_key in dupe_keys:
                        continue

                    dupe_keys.add(dupe_key)
                    read.is_duplicate = False
                    out_bam.write(read)


def join(args, outs, chunk_defs, chunk_outs):
    outs.coerce_strings()

    outs.clusters = []
    outs.filtered_bams = []
    for d, o in zip(chunk_defs, chunk_outs):
        outs.clusters.append(d.cluster_id)
        outs.filtered_bams.append(o.filtered_bam)


def build_loci(bam, chunk_size=5 * 10 ** 7):
    """
    Given a BAM path, builds a set of loci
    :param bam: Path to BAM
    :param chunk_size: Number of bases to pack into one bin
    :return:
    """
    s = pysam.Samfile(bam)
    # greedy implementation of the bin packing problem
    loci = []
    this_locus = []
    bin_size = 0
    for chrom, chrom_length in zip(s.header.references, s.header.lengths):
        region_start = 0
        while region_start < chrom_length:
            start = region_start
            end = min(region_start + chunk_size, chrom_length)
            this_locus.append([chrom, start, end])
            bin_size += end - start
            if bin_size >= chunk_size:
                loci.append(':'.join([','.join(map(str, x)) for x in this_locus]))
                this_locus = []
                bin_size = 0
            region_start = end
    return loci
