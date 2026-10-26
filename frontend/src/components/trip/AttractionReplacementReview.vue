<template>
  <a-modal :open="!!candidate" title="预览景点替换" ok-text="确认加入编辑草稿" cancel-text="保留原景点" @ok="emit('confirm')" @cancel="emit('cancel')">
    <p>原景点：{{ original?.name }}</p><p>拟替换为：{{ candidate?.name }}</p><p>{{ candidate?.address }}</p>
    <a-alert type="warning" message="确认只修改本地编辑草稿；点击保存后才写入行程。旧时间轴将失效，营业、预约、费用与新路线须重新核验。锁定和已完成安排不能在此替换。" />
  </a-modal>
</template>
<script setup lang="ts">
import type { Attraction, POIInfo } from '@/types'
defineProps<{ original?: Attraction; candidate?: POIInfo }>()
const emit = defineEmits<{ confirm: []; cancel: [] }>()
</script>
