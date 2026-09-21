/* Host-only ABI/layout probe for the pinned S5E9925 NCP-v25 header. */
#include <stddef.h>
#include <stdio.h>

typedef signed char s8;
typedef unsigned char u8;
typedef unsigned int u32;
typedef int s32;

#include "../../lineage/android_kernel_samsung_s5e9925/drivers/vision/npu/core/include/ncp_header_v25.h"

#define CHECK_SIZE(type, expected) _Static_assert(sizeof(type) == (expected), #type " size")
CHECK_SIZE(struct address_vector, 16);
CHECK_SIZE(struct memory_vector, 36);
CHECK_SIZE(struct pwr_est_vector, 16);
CHECK_SIZE(struct interruption_vector, 28);
CHECK_SIZE(struct group_vector, 44);
CHECK_SIZE(struct thread_vector, 28);
CHECK_SIZE(struct llc_vector, 12);
CHECK_SIZE(struct ncp_header, 172);

#define CHECK_OFFSET(type, field, expected) \
    _Static_assert(offsetof(type, field) == (expected), #type "::" #field " offset")
CHECK_OFFSET(struct memory_vector, address_vector_index, 32);
CHECK_OFFSET(struct group_vector, intrinsic_offset, 28);
CHECK_OFFSET(struct group_vector, intrinsic_size, 32);
CHECK_OFFSET(struct group_vector, isa_offset, 36);
CHECK_OFFSET(struct group_vector, isa_size, 40);
CHECK_OFFSET(struct thread_vector, group_str_idx, 12);
CHECK_OFFSET(struct thread_vector, group_end_idx, 16);
CHECK_OFFSET(struct thread_vector, interruption_str_idx, 20);
CHECK_OFFSET(struct thread_vector, interruption_end_idx, 24);
CHECK_OFFSET(struct ncp_header, magic_number1, 0);
CHECK_OFFSET(struct ncp_header, hdr_version, 4);
CHECK_OFFSET(struct ncp_header, hdr_size, 8);
CHECK_OFFSET(struct ncp_header, address_vector_offset, 60);
CHECK_OFFSET(struct ncp_header, address_vector_cnt, 64);
CHECK_OFFSET(struct ncp_header, memory_vector_offset, 68);
CHECK_OFFSET(struct ncp_header, memory_vector_cnt, 72);
CHECK_OFFSET(struct ncp_header, pwrest_vector_offset, 76);
CHECK_OFFSET(struct ncp_header, pwrest_vector_cnt, 80);
CHECK_OFFSET(struct ncp_header, interruption_vector_offset, 84);
CHECK_OFFSET(struct ncp_header, interruption_vector_cnt, 88);
CHECK_OFFSET(struct ncp_header, group_vector_offset, 92);
CHECK_OFFSET(struct ncp_header, group_vector_cnt, 96);
CHECK_OFFSET(struct ncp_header, thread_vector_offset, 100);
CHECK_OFFSET(struct ncp_header, thread_vector_cnt, 104);
CHECK_OFFSET(struct ncp_header, body_offset, 112);
CHECK_OFFSET(struct ncp_header, body_size, 116);
CHECK_OFFSET(struct ncp_header, io_vector_offset, 120);
CHECK_OFFSET(struct ncp_header, io_vector_cnt, 124);
CHECK_OFFSET(struct ncp_header, rq_vector_offset, 128);
CHECK_OFFSET(struct ncp_header, rq_vector_size, 132);
CHECK_OFFSET(struct ncp_header, llc_vector_offset, 140);
CHECK_OFFSET(struct ncp_header, llc_vector_cnt, 144);
CHECK_OFFSET(struct ncp_header, magic_number2, 168);

int main(void) {
    printf("{\"ncp_header\":%zu,\"address_vector\":%zu,\"memory_vector\":%zu,\"pwr_est_vector\":%zu,\"interruption_vector\":%zu,\"group_vector\":%zu,\"thread_vector\":%zu,\"llc_vector\":%zu,\"memory_address_index\":%zu,\"memory_type_cucode\":%u,\"memory_type_weight\":%u,\"memory_type_wmask\":%u}\n",
           sizeof(struct ncp_header), sizeof(struct address_vector),
           sizeof(struct memory_vector), sizeof(struct pwr_est_vector),
           sizeof(struct interruption_vector), sizeof(struct group_vector),
           sizeof(struct thread_vector), sizeof(struct llc_vector),
           offsetof(struct memory_vector, address_vector_index),
           MEMORY_TYPE_CUCODE, MEMORY_TYPE_WEIGHT, MEMORY_TYPE_WMASK);
    printf("{\"magic1\":%zu,\"version\":%zu,\"header_size\":%zu,\"address_offset\":%zu,\"address_count\":%zu,\"memory_offset\":%zu,\"memory_count\":%zu,\"power_offset\":%zu,\"power_count\":%zu,\"interrupt_offset\":%zu,\"interrupt_count\":%zu,\"group_offset\":%zu,\"group_count\":%zu,\"thread_offset\":%zu,\"thread_count\":%zu,\"body_offset\":%zu,\"body_size\":%zu,\"io_offset\":%zu,\"io_count\":%zu,\"rq_offset\":%zu,\"rq_size\":%zu,\"llc_offset\":%zu,\"llc_count\":%zu,\"magic2\":%zu}\n",
           offsetof(struct ncp_header, magic_number1), offsetof(struct ncp_header, hdr_version),
           offsetof(struct ncp_header, hdr_size), offsetof(struct ncp_header, address_vector_offset),
           offsetof(struct ncp_header, address_vector_cnt), offsetof(struct ncp_header, memory_vector_offset),
           offsetof(struct ncp_header, memory_vector_cnt), offsetof(struct ncp_header, pwrest_vector_offset),
           offsetof(struct ncp_header, pwrest_vector_cnt), offsetof(struct ncp_header, interruption_vector_offset),
           offsetof(struct ncp_header, interruption_vector_cnt), offsetof(struct ncp_header, group_vector_offset),
           offsetof(struct ncp_header, group_vector_cnt), offsetof(struct ncp_header, thread_vector_offset),
           offsetof(struct ncp_header, thread_vector_cnt), offsetof(struct ncp_header, body_offset),
           offsetof(struct ncp_header, body_size), offsetof(struct ncp_header, io_vector_offset),
           offsetof(struct ncp_header, io_vector_cnt), offsetof(struct ncp_header, rq_vector_offset),
           offsetof(struct ncp_header, rq_vector_size), offsetof(struct ncp_header, llc_vector_offset),
           offsetof(struct ncp_header, llc_vector_cnt), offsetof(struct ncp_header, magic_number2));
    printf("{\"memory_address_index\":%zu,\"group_intrinsic_offset\":%zu,\"group_intrinsic_size\":%zu,\"group_isa_offset\":%zu,\"group_isa_size\":%zu,\"thread_group_start\":%zu,\"thread_group_end\":%zu,\"thread_interrupt_start\":%zu,\"thread_interrupt_end\":%zu}\n",
           offsetof(struct memory_vector, address_vector_index),
           offsetof(struct group_vector, intrinsic_offset), offsetof(struct group_vector, intrinsic_size),
           offsetof(struct group_vector, isa_offset), offsetof(struct group_vector, isa_size),
           offsetof(struct thread_vector, group_str_idx), offsetof(struct thread_vector, group_end_idx),
           offsetof(struct thread_vector, interruption_str_idx), offsetof(struct thread_vector, interruption_end_idx));
    return 0;
}
