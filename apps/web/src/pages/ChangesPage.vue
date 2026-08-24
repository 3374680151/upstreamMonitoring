<script setup lang="ts">
import PageHeader from "@/components/PageHeader.vue";
import Panel from "@/components/Panel.vue";
import ChangeTable from "@/components/ChangeTable.vue";
import { useConsoleData } from "@/composables/useConsoleData";

const { changes, sites } = useConsoleData();
</script>

<template>
  <div class="upstream-rise flex flex-col gap-6 md:gap-8">
    <PageHeader
      title="变化记录"
      subtitle="所有渠道最近变化，方便快速扫一眼是否有上游改价。"
    />
    <Panel title="全局变化记录" :subtitle="`${changes.length} 条`">
      <ChangeTable :changes="changes" :sites="sites" />
    </Panel>
  </div>
</template>
