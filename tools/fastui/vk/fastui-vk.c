// fastui-vk: Samsung Vulkan (Xclipse 920, vendor HAL via the isolated Bionic
// runtime and bridge-v4 libvulkan.so) frame renderer for the fastui spike.
//
// Connects to the native display process (fastui gpu) over a unix socket,
// then for each request renders one 1080x2340 XRGB8888 frame with a compute
// shader into a linear buffer and replies with timings. Buffer sharing modes:
//   0 export   Vulkan allocates dma-buf-exportable memory, sends the fds;
//              the display imports them into card1 (zero copy).
//   1 import   the display sends its dumb-buffer dma-bufs; Vulkan imports
//              them and renders straight into scanout memory (zero copy).
//   2 readback Vulkan renders into its own host-visible memory, then the CPU
//              copies the frame into the mmapped dumb-buffer dma-buf.
// Runs as uid 1000 with no capabilities inside the sandbox; spawns nothing.
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "grid.spv.h"

#define MAGIC 0x46535455u
static uint64_t now_us(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000u + (uint64_t)t.tv_nsec / 1000u;
}

static int sock_fd = -1;
static int send_msg(const uint32_t m[16], const int *fds, int nfds) {
    struct iovec iov = {(void *)m, 64};
    char cbuf[CMSG_SPACE(sizeof(int) * 8)];
    struct msghdr mh = {0};
    mh.msg_iov = &iov;
    mh.msg_iovlen = 1;
    if (nfds > 0) {
        mh.msg_control = cbuf;
        mh.msg_controllen = CMSG_SPACE(sizeof(int) * nfds);
        struct cmsghdr *c = CMSG_FIRSTHDR(&mh);
        c->cmsg_level = SOL_SOCKET;
        c->cmsg_type = SCM_RIGHTS;
        c->cmsg_len = CMSG_LEN(sizeof(int) * nfds);
        memcpy(CMSG_DATA(c), fds, sizeof(int) * nfds);
    }
    return sendmsg(sock_fd, &mh, 0) == 64 ? 0 : -1;
}
static int recv_msg(uint32_t m[16], int *fds, int *nfds) {
    struct iovec iov = {m, 64};
    char cbuf[CMSG_SPACE(sizeof(int) * 8)];
    struct msghdr mh = {0};
    mh.msg_iov = &iov;
    mh.msg_iovlen = 1;
    mh.msg_control = cbuf;
    mh.msg_controllen = sizeof cbuf;
    if (recvmsg(sock_fd, &mh, MSG_WAITALL) != 64) return -1;
    int n = 0;
    for (struct cmsghdr *c = CMSG_FIRSTHDR(&mh); c; c = CMSG_NXTHDR(&mh, c))
        if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS) {
            int k = (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
            memcpy(fds + n, CMSG_DATA(c), sizeof(int) * k);
            n += k;
        }
    if (nfds) *nfds = n;
    return 0;
}
static void fail(int code, VkResult r, const char *what) {
    fprintf(stderr, "fastui-vk: FAIL %s: vk=%d errno=%d\n", what, (int)r, errno);
    uint32_t m[16] = {MAGIC, (uint32_t)code};
    m[7] = (uint32_t)r;
    send_msg(m, NULL, 0);
    exit(1);
}

#define F(name) static PFN_##name name
F(vkGetInstanceProcAddr); F(vkCreateInstance); F(vkEnumeratePhysicalDevices); F(vkGetPhysicalDeviceProperties);
F(vkGetPhysicalDeviceQueueFamilyProperties); F(vkGetPhysicalDeviceMemoryProperties); F(vkEnumerateDeviceExtensionProperties);
F(vkCreateDevice); F(vkGetDeviceProcAddr); F(vkGetDeviceQueue); F(vkCreateBuffer); F(vkGetBufferMemoryRequirements);
F(vkAllocateMemory); F(vkBindBufferMemory); F(vkMapMemory); F(vkGetMemoryFdKHR); F(vkGetMemoryFdPropertiesKHR);
F(vkCreateShaderModule); F(vkCreateDescriptorSetLayout); F(vkCreatePipelineLayout); F(vkCreateComputePipelines);
F(vkCreateDescriptorPool); F(vkAllocateDescriptorSets); F(vkUpdateDescriptorSets); F(vkCreateCommandPool);
F(vkAllocateCommandBuffers); F(vkBeginCommandBuffer); F(vkEndCommandBuffer); F(vkCmdBindPipeline);
F(vkCmdBindDescriptorSets); F(vkCmdPushConstants); F(vkCmdDispatch); F(vkCmdPipelineBarrier); F(vkCreateFence);
F(vkResetFences); F(vkWaitForFences); F(vkQueueSubmit); F(vkResetCommandBuffer); F(vkDeviceWaitIdle);
F(vkInvalidateMappedMemoryRanges);

int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] : "/tmp/fastui.sock";
    int noop = argc > 2 && !strcmp(argv[2], "noop");   /* 1-workgroup dispatch: fixed submit/fence cost */
    sock_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    struct sockaddr_un sa = {.sun_family = AF_UNIX};
    strncpy(sa.sun_path, path, sizeof sa.sun_path - 1);
    for (int i = 0; connect(sock_fd, (struct sockaddr *)&sa, sizeof sa) != 0; i++) {
        if (i > 100) { perror("fastui-vk: connect"); return 1; }
        usleep(100000);
    }
    uint32_t hello[16];
    int in_fds[8], n_in = 0;
    if (recv_msg(hello, in_fds, &n_in) || hello[0] != MAGIC) { fprintf(stderr, "bad hello\n"); return 1; }
    uint32_t mode = hello[1], W = hello[2], H = hello[3], pitch = hello[4], nbuf = hello[5];
    uint64_t fbsize = (uint64_t)pitch * H;
    fprintf(stderr, "fastui-vk: mode=%u %ux%u pitch=%u nbuf=%u fds=%d\n", mode, W, H, pitch, nbuf, n_in);
    uint64_t t_init = now_us();

    void *lib = dlopen("libvulkan.so", RTLD_NOW | RTLD_LOCAL);
    if (!lib) { fprintf(stderr, "dlopen: %s\n", dlerror()); fail(2, 0, "dlopen libvulkan"); }
    vkGetInstanceProcAddr = (PFN_vkGetInstanceProcAddr)dlsym(lib, "vkGetInstanceProcAddr");
#define GI(inst, n) n = (PFN_##n)vkGetInstanceProcAddr(inst, #n)
    GI(NULL, vkCreateInstance);
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "fastui-vk", 1, "fastui", 1, VK_API_VERSION_1_1};
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app, 0, NULL, 0, NULL};
    VkInstance inst;
    VkResult r = vkCreateInstance(&ici, NULL, &inst);
    if (r) fail(3, r, "vkCreateInstance");
    GI(inst, vkEnumeratePhysicalDevices); GI(inst, vkGetPhysicalDeviceProperties); GI(inst, vkGetPhysicalDeviceQueueFamilyProperties);
    GI(inst, vkGetPhysicalDeviceMemoryProperties); GI(inst, vkEnumerateDeviceExtensionProperties); GI(inst, vkCreateDevice);
    GI(inst, vkGetDeviceProcAddr);
    uint32_t npd = 1;
    VkPhysicalDevice pd;
    r = vkEnumeratePhysicalDevices(inst, &npd, &pd);
    if (r < 0 || npd == 0) fail(4, r, "no physical device");
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(pd, &props);
    fprintf(stderr, "fastui-vk: device %s api %u.%u\n", props.deviceName, VK_VERSION_MAJOR(props.apiVersion), VK_VERSION_MINOR(props.apiVersion));
    uint32_t nq = 8;
    VkQueueFamilyProperties qf[8];
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &nq, qf);
    uint32_t qfi = 0;
    for (uint32_t i = 0; i < nq; i++) if (qf[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { qfi = i; break; }
    // Device extensions: enable the external-memory set if present.
    uint32_t next = 0;
    vkEnumerateDeviceExtensionProperties(pd, NULL, &next, NULL);
    VkExtensionProperties *ext = calloc(next, sizeof *ext);
    vkEnumerateDeviceExtensionProperties(pd, NULL, &next, ext);
    const char *want[] = {"VK_KHR_external_memory", "VK_KHR_external_memory_fd", "VK_EXT_external_memory_dma_buf", "VK_EXT_queue_family_foreign"};
    const char *on[4];
    uint32_t non = 0;
    for (int w = 0; w < 4; w++)
        for (uint32_t i = 0; i < next; i++)
            if (!strcmp(ext[i].extensionName, want[w])) { on[non++] = want[w]; break; }
    fprintf(stderr, "fastui-vk: %u device extensions; enabling %u external-memory ones\n", next, non);
    for (uint32_t i = 0; i < non; i++) fprintf(stderr, "fastui-vk:   %s\n", on[i]);
    if (mode != 2 && non < 3) fail(5, 0, "external memory dma_buf extensions missing");
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, qfi, 1, &prio};
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, NULL, 0, 1, &qci, 0, NULL, non, on, NULL};
    VkDevice dev;
    r = vkCreateDevice(pd, &dci, NULL, &dev);
    if (r) fail(6, r, "vkCreateDevice");
#define GD(n) n = (PFN_##n)vkGetDeviceProcAddr(dev, #n)
    GD(vkGetDeviceQueue); GD(vkCreateBuffer); GD(vkGetBufferMemoryRequirements); GD(vkAllocateMemory); GD(vkBindBufferMemory);
    GD(vkMapMemory); GD(vkGetMemoryFdKHR); GD(vkGetMemoryFdPropertiesKHR); GD(vkCreateShaderModule); GD(vkCreateDescriptorSetLayout);
    GD(vkCreatePipelineLayout); GD(vkCreateComputePipelines); GD(vkCreateDescriptorPool); GD(vkAllocateDescriptorSets);
    GD(vkUpdateDescriptorSets); GD(vkCreateCommandPool); GD(vkAllocateCommandBuffers); GD(vkBeginCommandBuffer);
    GD(vkEndCommandBuffer); GD(vkCmdBindPipeline); GD(vkCmdBindDescriptorSets); GD(vkCmdPushConstants); GD(vkCmdDispatch);
    GD(vkCmdPipelineBarrier); GD(vkCreateFence); GD(vkResetFences); GD(vkWaitForFences); GD(vkQueueSubmit);
    GD(vkResetCommandBuffer); GD(vkDeviceWaitIdle); GD(vkInvalidateMappedMemoryRanges);
    VkQueue q;
    vkGetDeviceQueue(dev, qfi, 0, &q);
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(pd, &mp);
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
        fprintf(stderr, "fastui-vk: memtype %u heap %u flags 0x%x\n", i, mp.memoryTypes[i].heapIndex, mp.memoryTypes[i].propertyFlags);

    VkBuffer buf[2];
    VkDeviceMemory mem[2];
    void *host[2] = {0}, *dst[2] = {0};
    int out_fds[2];
    uint32_t chosen = 0;
    const VkExternalMemoryHandleTypeFlagBits HT = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
    for (uint32_t b = 0; b < nbuf && b < 2; b++) {
        VkExternalMemoryBufferCreateInfo ebi = {VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_BUFFER_CREATE_INFO, NULL, HT};
        VkBufferCreateInfo bci = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, mode == 2 ? NULL : &ebi, 0, fbsize,
                                  VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, VK_SHARING_MODE_EXCLUSIVE, 0, NULL};
        r = vkCreateBuffer(dev, &bci, NULL, &buf[b]);
        if (r) fail(7, r, "vkCreateBuffer");
        VkMemoryRequirements mr;
        vkGetBufferMemoryRequirements(dev, buf[b], &mr);
        uint32_t bits = mr.memoryTypeBits;
        VkMemoryAllocateInfo mai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, mr.size, 0};
        VkExportMemoryAllocateInfo emi = {VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO, NULL, HT};
        VkImportMemoryFdInfoKHR imi = {VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR, NULL, HT, -1};
        VkMemoryPropertyFlags want_flags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
        if (mode == 0) {
            mai.pNext = &emi;
        } else if (mode == 1) {
            VkMemoryFdPropertiesKHR fp = {VK_STRUCTURE_TYPE_MEMORY_FD_PROPERTIES_KHR, NULL, 0};
            r = vkGetMemoryFdPropertiesKHR(dev, HT, in_fds[b], &fp);
            if (r) fail(8, r, "vkGetMemoryFdPropertiesKHR");
            fprintf(stderr, "fastui-vk: dma-buf %d importable memtype bits 0x%x (buffer wants 0x%x, size %llu)\n",
                    in_fds[b], fp.memoryTypeBits, bits, (unsigned long long)mr.size);
            bits &= fp.memoryTypeBits;
            imi.fd = in_fds[b];
            mai.pNext = &imi;
            want_flags = 0;
        } else {
            want_flags = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT;
        }
        int mt = -1;
        for (uint32_t i = 0; i < mp.memoryTypeCount && mt < 0; i++)
            if ((bits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & want_flags) == want_flags) mt = i;
        for (uint32_t i = 0; i < mp.memoryTypeCount && mt < 0; i++)
            if ((bits & (1u << i)) && (mode != 2 || (mp.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT))) mt = i;
        if (mt < 0) fail(9, 0, "no memory type");
        mai.memoryTypeIndex = chosen = mt;
        r = vkAllocateMemory(dev, &mai, NULL, &mem[b]);
        if (r) fail(10, r, "vkAllocateMemory");
        r = vkBindBufferMemory(dev, buf[b], mem[b], 0);
        if (r) fail(11, r, "vkBindBufferMemory");
        if (mode == 0) {
            VkMemoryGetFdInfoKHR gfi = {VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR, NULL, mem[b], HT};
            r = vkGetMemoryFdKHR(dev, &gfi, &out_fds[b]);
            if (r) fail(12, r, "vkGetMemoryFdKHR");
        } else if (mode == 2) {
            r = vkMapMemory(dev, mem[b], 0, VK_WHOLE_SIZE, 0, &host[b]);
            if (r) fail(13, r, "vkMapMemory");
            dst[b] = mmap(NULL, fbsize, PROT_READ | PROT_WRITE, MAP_SHARED, in_fds[b], 0);
            if (dst[b] == MAP_FAILED) fail(14, 0, "mmap shared readback memfd");
        }
    }

    // Compute pipeline
    VkShaderModuleCreateInfo smi = {VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, NULL, 0, sizeof grid_spv, (const uint32_t *)grid_spv};
    VkShaderModule sm;
    if ((r = vkCreateShaderModule(dev, &smi, NULL, &sm))) fail(15, r, "shader");
    VkDescriptorSetLayoutBinding lb = {0, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, NULL};
    VkDescriptorSetLayoutCreateInfo dli = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, NULL, 0, 1, &lb};
    VkDescriptorSetLayout dsl;
    if ((r = vkCreateDescriptorSetLayout(dev, &dli, NULL, &dsl))) fail(16, r, "dsl");
    VkPushConstantRange pcr = {VK_SHADER_STAGE_COMPUTE_BIT, 0, 20};
    VkPipelineLayoutCreateInfo pli = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, NULL, 0, 1, &dsl, 1, &pcr};
    VkPipelineLayout pl;
    if ((r = vkCreatePipelineLayout(dev, &pli, NULL, &pl))) fail(17, r, "pl");
    VkComputePipelineCreateInfo cpi = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO, NULL, 0,
                                       {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, NULL, 0, VK_SHADER_STAGE_COMPUTE_BIT, sm, "main", NULL}, pl, VK_NULL_HANDLE, 0};
    VkPipeline pipe;
    if ((r = vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cpi, NULL, &pipe))) fail(18, r, "pipeline");
    VkDescriptorPoolSize ps = {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 2};
    VkDescriptorPoolCreateInfo dpi = {VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, NULL, 0, 2, 1, &ps};
    VkDescriptorPool dp;
    if ((r = vkCreateDescriptorPool(dev, &dpi, NULL, &dp))) fail(19, r, "pool");
    VkDescriptorSetLayout dsls[2] = {dsl, dsl};
    VkDescriptorSetAllocateInfo dai = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, NULL, dp, 2, dsls};
    VkDescriptorSet ds[2];
    if ((r = vkAllocateDescriptorSets(dev, &dai, ds))) fail(20, r, "sets");
    for (int b = 0; b < 2; b++) {
        VkDescriptorBufferInfo dbi = {buf[b], 0, VK_WHOLE_SIZE};
        VkWriteDescriptorSet wds = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, NULL, ds[b], 0, 0, 1, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, NULL, &dbi, NULL};
        vkUpdateDescriptorSets(dev, 1, &wds, 0, NULL);
    }
    VkCommandPoolCreateInfo cpci = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, qfi};
    VkCommandPool cp;
    if ((r = vkCreateCommandPool(dev, &cpci, NULL, &cp))) fail(21, r, "cmdpool");
    VkCommandBufferAllocateInfo cbai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, cp, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
    VkCommandBuffer cb;
    if ((r = vkAllocateCommandBuffers(dev, &cbai, &cb))) fail(22, r, "cmdbuf");
    VkFenceCreateInfo fci = {VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, NULL, 0};
    VkFence fence;
    if ((r = vkCreateFence(dev, &fci, NULL, &fence))) fail(23, r, "fence");

    uint32_t ack[16] = {MAGIC, 0, pitch, 2, (uint32_t)fbsize, (uint32_t)(fbsize >> 32)};
    ack[8] = chosen;
    ack[9] = mp.memoryTypes[chosen].propertyFlags;
    ack[10] = (uint32_t)(now_us() - t_init);
    if (send_msg(ack, out_fds, mode == 0 ? 2 : 0)) { perror("send ack"); return 1; }
    fprintf(stderr, "fastui-vk: ready in %.1f ms (memtype %u flags 0x%x)\n", (now_us() - t_init) / 1000.0, chosen, ack[9]);

    for (;;) {
        uint32_t cmd[16];
        if (recv_msg(cmd, NULL, NULL) || cmd[0] == 0) break;
        uint32_t b = cmd[1] & 1;
        uint64_t t0 = now_us();
        uint32_t pcv[5] = {W, H, pitch / 4, cmd[2], cmd[3]};
        vkResetCommandBuffer(cb, 0);
        VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL, VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT, NULL};
        vkBeginCommandBuffer(cb, &bi);
        vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
        vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pl, 0, 1, &ds[b], 0, NULL);
        vkCmdPushConstants(cb, pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, 20, pcv);
        if (noop) vkCmdDispatch(cb, 1, 1, 1); else vkCmdDispatch(cb, (W + 15) / 16, (H + 15) / 16, 1);
        VkMemoryBarrier mb = {VK_STRUCTURE_TYPE_MEMORY_BARRIER, NULL, VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_HOST_READ_BIT | VK_ACCESS_MEMORY_READ_BIT};
        vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_HOST_BIT | VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
        vkEndCommandBuffer(cb);
        VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cb, 0, NULL};
        vkResetFences(dev, 1, &fence);
        r = vkQueueSubmit(q, 1, &si, fence);
        if (!r) r = vkWaitForFences(dev, 1, &fence, VK_TRUE, 2000000000ull);
        uint64_t t1 = now_us();
        uint64_t t2 = t1;
        if (!r && mode == 2) {
            if (!(mp.memoryTypes[chosen].propertyFlags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
                VkMappedMemoryRange mr = {VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE, NULL, mem[b], 0, VK_WHOLE_SIZE};
                vkInvalidateMappedMemoryRanges(dev, 1, &mr);
            }
            memcpy(dst[b], host[b], fbsize);
            t2 = now_us();
        }
        uint32_t rep[16] = {2, (uint32_t)r, (uint32_t)(t1 - t0), (uint32_t)(t2 - t1)};
        if (send_msg(rep, NULL, 0)) break;
        if (r) { fprintf(stderr, "fastui-vk: frame failed vk=%d\n", r); break; }
    }
    vkDeviceWaitIdle(dev);
    fprintf(stderr, "fastui-vk: done\n");
    return 0;
}
